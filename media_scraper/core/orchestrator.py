"""
流水线编排模块
完整流水线: 扫描 → 解析 → 刮削 → 元数据写入 → 重命名计划
"""

import asyncio
import csv
import json
import logging
import os
import time
from typing import Callable, Dict, List, Optional

from core.config import load_config
from core.http_sessions import close_all_sessions
from core.models import Metadata, ParsedFilename, ProgressInfo, RenamePlan, VideoType
from core.parser import parse_filename, settle_local_download_indexes
from core.parts import assign_part_indexes
from core.pipeline_apply import (
    COMPANION_EXTENSIONS,
    POSTER_SUFFIXES,
    apply_plans_async,
    copy_companion_files,
    ensure_metadata_in_folder,
    rollback as rollback_from_log,
    save_rollback_log,
)
from core.pipeline_persist import persist_sidecars_and_posters
from core.progress import ProgressTracker
from core.scanner import collect_videos
from providers.aggregator import ProviderAggregator
from rename.renamer import (
    apply_long_name_choices,
    build_rename_plan,
    resolve_plan_conflicts,
)

logger = logging.getLogger(__name__)


class Pipeline:
    """刮削流水线编排器。

    完整流程:
    1. 扫描目录 → 发现视频文件
    2. 解析文件名 → 提取标题/年份/番号/分辨率
    3. 刮削元数据 → 按 Provider 优先级搜索
    4. 写入元数据 → NFO + JSON + 封面
    5. 生成重命名计划 → dry-run 预览
    """

    COMPANION_EXTENSIONS = COMPANION_EXTENSIONS
    POSTER_SUFFIXES = POSTER_SUFFIXES

    def __init__(self, config: Optional[dict] = None):
        self.config = config or load_config()
        self.tracker = ProgressTracker()

        self.video_files: List[str] = []
        self.parsed_results: List[ParsedFilename] = []
        self.scraped_metadata: List[Optional[Metadata]] = []
        self.rename_plans: List[RenamePlan] = []
        self._apply_log: List[dict] = []
        self._commit_guard = False

        self._progress_callback: Optional[Callable[[ProgressInfo], None]] = None
        self._file_event_callback: Optional[Callable[[dict], None]] = None
        self._long_name_callback: Optional[Callable[[list], Optional[dict]]] = None
        self._cancel_event = None

    def set_progress_callback(self, callback: Callable[[ProgressInfo], None]) -> None:
        self._progress_callback = callback
        self.tracker.set_callback(callback)

    def set_file_event_callback(self, callback: Callable[[dict], None]) -> None:
        self._file_event_callback = callback

    def set_long_name_callback(self, callback: Callable[[list], Optional[dict]]) -> None:
        """文件名超长时调用。回调返回 {源路径: 新主名}；返回 None 表示取消本次写入。"""
        self._long_name_callback = callback

    def _emit_file_event(self, event: dict) -> None:
        if self._file_event_callback is not None:
            try:
                self._file_event_callback(event)
            except Exception:
                logger.exception("文件事件回调异常")

    def set_cancel_event(self, event) -> None:
        self._cancel_event = event

    def _is_cancelled(self) -> bool:
        return bool(self._cancel_event is not None and self._cancel_event.is_set())

    async def _check_cancel(self) -> None:
        if self._commit_guard:
            return
        if self._is_cancelled():
            logger.warning("收到中断请求，停止后续任务。")
            raise asyncio.CancelledError()

    async def run_full_async(
        self, directory: str, output_csv: Optional[str] = None,
        persist_metadata: bool = False,
    ) -> List[RenamePlan]:
        """异步入口：刮削并生成计划。默认不写 NFO/JSON/封面。"""
        return await self._run_full(
            directory, output_csv, persist_metadata=persist_metadata,
        )

    async def run_apply_async(
        self, directory: str, output_csv: Optional[str] = None
    ) -> List[RenamePlan]:
        """异步入口：刮削 + 写入元数据 + 实际移动（供 GUI worker 调用）。"""
        self._start_time = time.time()
        logger.info("=" * 60)
        logger.info("开始执行流水线（实际执行模式）")
        logger.info("目标目录: %s", directory)
        await self._run_full(directory, output_csv, persist_metadata=True)
        return self.rename_plans

    def run_dry(
        self,
        directory: str,
        output_csv: Optional[str] = None,
        do_scrape: bool = False,
    ) -> List[RenamePlan]:
        if do_scrape:
            return asyncio.run(self._run_full(
                directory, output_csv, persist_metadata=False,
            ))
        return self._run_scan_only(directory, output_csv)

    def run_apply(
        self,
        directory: str,
        output_csv: Optional[str] = None,
        do_scrape: bool = True,
    ) -> List[dict]:
        if do_scrape:
            plans = asyncio.run(self._run_full(
                directory, output_csv, persist_metadata=True,
            ))
            if not plans:
                logger.info("无重命名计划，跳过执行")
                return []
            return list(self._apply_log)
        else:
            plans = self._run_scan_only(directory, output_csv)

        if not plans:
            logger.info("无重命名计划，跳过执行")
            return []
        return self._apply_plans(plans)

    def _apply_plans(self, plans: List[RenamePlan]) -> List[dict]:
        return asyncio.run(self._apply_plans_async(plans))

    async def _apply_plans_async(
        self,
        plans: List[RenamePlan],
        honor_cancel: bool = True,
        write_rollback: bool = True,
    ) -> List[dict]:
        return await apply_plans_async(
            self, plans, honor_cancel=honor_cancel, write_rollback=write_rollback,
        )

    def _copy_companion_files(self, src_video: str, dst_dir: str) -> list:
        return copy_companion_files(src_video, dst_dir)

    def _ensure_metadata_in_folder(self, folder: str, plan: RenamePlan) -> None:
        ensure_metadata_in_folder(folder, plan)

    def _save_rollback_log(self, log: List[dict], directory: str) -> str:
        return save_rollback_log(log, directory)

    @staticmethod
    def rollback(log_path: str) -> List[dict]:
        return rollback_from_log(log_path)

    def _scan_and_parse(self, directory) -> bool:
        """扫描并解析文件名。没有视频时返回 False。

        directory 可以是一个目录、一个视频文件，或它们组成的列表。
        """
        scan_cfg = self.config.get("scan", {})
        sources = [directory] if isinstance(directory, str) else list(directory)
        self.tracker.start_phase("scanning")
        self.video_files = collect_videos(
            sources,
            video_extensions=set(scan_cfg.get("video_extensions", [])) or None,
            scan_subdirs=scan_cfg.get("scan_subdirs", True),
            exclude_dirs=set(scan_cfg.get("exclude_dirs", [])),
            min_file_size_mb=float(scan_cfg.get("min_file_size_mb", 0) or 0),
        )
        self.tracker.total_files = len(self.video_files)
        self.tracker.update(
            current=len(self.video_files),
            message=f"扫描完成，发现 {len(self.video_files)} 个视频文件",
        )
        logger.info("扫描完成: %d 个视频文件", len(self.video_files))
        self.parsed_results = []
        if not self.video_files:
            return False

        self.tracker.start_phase("parsing")
        for i, fpath in enumerate(self.video_files):
            parsed = parse_filename(fpath)
            self.parsed_results.append(parsed)
            self.tracker.update(
                current=i + 1,
                message=f"解析: {os.path.basename(fpath)}",
                file_name=os.path.basename(fpath),
            )
        merge_parts = self.config.get("rename", {}).get("merge_parts", True)
        assign_part_indexes(self.parsed_results, enabled=bool(merge_parts))
        settle_local_download_indexes(self.parsed_results)
        logger.info("解析完成: %d 个文件", len(self.parsed_results))
        return True

    @staticmethod
    def _metadata_for_local_title(parsed: ParsedFilename) -> Optional[Metadata]:
        """无番号的中日文标题不查网上数据库，直接用文件名做本地资料。"""
        if parsed.video_type != VideoType.LOCAL:
            return None
        title = (parsed.title or "").strip()
        if not title:
            return None
        return Metadata(
            title=title,
            source_provider="本地",
            extra={"local_only": True, "lockdata": True},
        )

    def _run_scan_only(
        self, directory: str, output_csv: Optional[str] = None
    ) -> List[RenamePlan]:
        rename_cfg = self.config.get("rename", {})
        self._scan_and_parse(directory)

        self.scraped_metadata = [
            self._metadata_for_local_title(parsed) for parsed in self.parsed_results
        ]
        self._build_rename_plans(rename_cfg)
        if output_csv:
            self.export_csv(output_csv)
        self.tracker.finish("预览完成")
        logger.info("流水线完成，共生成 %d 个重命名计划", len(self.rename_plans))
        return self.rename_plans

    async def _run_full(
        self, directory: str, output_csv: Optional[str] = None,
        persist_metadata: bool = False,
    ) -> List[RenamePlan]:
        """完整流水线：扫描 → 解析 → 刮削 →（执行时）写入元数据。"""
        rename_cfg = self.config.get("rename", {})

        if not self._scan_and_parse(directory):
            self.tracker.finish("未发现视频")
            return []

        self._emit_file_event({
            "type": "file_list",
            "files": [
                {
                    "index": idx,
                    "file_name": os.path.basename(parsed.original_path),
                    "code": getattr(parsed, "code", "") or "",
                }
                for idx, parsed in enumerate(self.parsed_results)
            ],
        })

        self.scraped_metadata = [
            self._metadata_for_local_title(parsed) for parsed in self.parsed_results
        ]
        local_indexes = [
            idx for idx, meta in enumerate(self.scraped_metadata) if meta is not None
        ]
        for idx in local_indexes:
            logger.info(
                "无番号，按标题本地归档: %s",
                os.path.basename(self.parsed_results[idx].original_path),
            )
            self._emit_file_event({
                "type": "file",
                "index": idx,
                "status": "running",
                "stage": 60,
            })

        self.tracker.start_phase("searching")
        if persist_metadata:
            await self._apply_streaming(directory, rename_cfg, output_csv)
            return self.rename_plans

        if not any(meta is None for meta in self.scraped_metadata):
            self.tracker.update(
                current=len(self.parsed_results),
                message=f"无番号文件按标题归档（{len(self.parsed_results)}）",
            )
            self._build_rename_plans(rename_cfg)
            if persist_metadata:
                await self._commit_ordered()
            if output_csv:
                self.export_csv(output_csv)
            if persist_metadata:
                logger.info("完整流水线完成，共处理 %d 个文件", len(self.video_files))
            else:
                self.tracker.finish("预览完成，未写入刮削文件")
                logger.info(
                    "预览完成，共 %d 个计划（未写入刮削文件）", len(self.video_files),
                )
            return self.rename_plans

        aggregator = ProviderAggregator(self.config)
        from core.page_cache import get_page_cache
        get_page_cache(self.config)

        from core.proxy import get_proxy, test_proxy
        raw_proxy = self.config.get("http", {}).get("proxy", "")
        if get_proxy(self.config):
            preflight = await test_proxy(raw_proxy, timeout=5.0)
            if not preflight.get("ok"):
                logger.error(
                    "代理连通性预检失败：%s。请在 设置-网络代理 中检查配置；"
                    "本次刮削很可能无法获取在线数据。",
                    preflight.get("message"),
                )
                self.tracker.update(
                    current=0,
                    message="代理不可用：" + str(preflight.get("message")),
                )
            else:
                logger.info("代理预检通过：%s", preflight.get("message"))

        concurrency_cfg = self.config.get("concurrency", {})
        max_workers = concurrency_cfg.get("max_workers", 10)
        semaphore = asyncio.Semaphore(max_workers)
        processed_count = len(local_indexes)
        leader_for: dict[int, int] = {}
        seen_groups: dict[str, int] = {}
        scrape_indexes: list[int] = []
        for index, parsed in enumerate(self.parsed_results):
            if self.scraped_metadata[index] is not None:
                continue
            group = parsed.part_group
            if group and group in seen_groups:
                leader_for[index] = seen_groups[group]
            else:
                if group:
                    seen_groups[group] = index
                scrape_indexes.append(index)
        followers_of: dict[int, list[int]] = {}
        for index, leader in leader_for.items():
            followers_of.setdefault(leader, []).append(index)

        async def scrape_with_limit(idx, parsed):
            nonlocal processed_count
            async with semaphore:
                fname = os.path.basename(parsed.original_path)
                self._emit_file_event({
                    "type": "file",
                    "index": idx,
                    "status": "running",
                    "stage": 30,
                })
                try:
                    metadata = await aggregator.scrape(parsed)
                    self.scraped_metadata[idx] = metadata
                    if metadata:
                        title = metadata.title or metadata.code or ""
                        logger.info("刮削成功 [%s]: %s", fname, title[:60])
                        self._emit_file_event({
                            "type": "file",
                            "index": idx,
                            "status": "running",
                            "stage": 60,
                        })
                    else:
                        logger.warning("未获取到元数据 [%s]", fname)
                        self._emit_file_event({
                            "type": "file",
                            "index": idx,
                            "status": "skipped",
                            "stage": 0,
                        })
                except Exception as exc:
                    logger.error("刮削失败 [%s]: %s", fname, exc)
                    self._emit_file_event({
                        "type": "file",
                        "index": idx,
                        "status": "failed",
                        "stage": 30,
                    })
                finally:
                    followers = followers_of.get(idx, [])
                    for follower in followers:
                        self.scraped_metadata[follower] = self.scraped_metadata[idx]
                        followed = self.scraped_metadata[follower]
                        self._emit_file_event({
                            "type": "file",
                            "index": follower,
                            "status": "running" if followed else "skipped",
                            "stage": 60 if followed else 0,
                        })
                    processed_count += 1 + len(followers)
                    self.tracker.update(
                        current=processed_count,
                        message=f"搜索中 ({processed_count}/{self.tracker.total_files})",
                        file_name=fname,
                    )

        await self._check_cancel()
        tasks = [
            scrape_with_limit(i, self.parsed_results[i]) for i in scrape_indexes
        ]
        gather_task = asyncio.gather(*tasks)

        async def _cancel_watcher():
            while not self._is_cancelled():
                await asyncio.sleep(0.1)
            gather_task.cancel()

        watcher = asyncio.ensure_future(_cancel_watcher())
        try:
            await gather_task
        except asyncio.CancelledError:
            await self._check_cancel()
            raise
        finally:
            watcher.cancel()

        self._build_rename_plans(rename_cfg)
        if persist_metadata:
            await self._commit_ordered()

        await close_all_sessions()

        if output_csv:
            self.export_csv(output_csv)

        if persist_metadata:
            logger.info("完整流水线完成，共处理 %d 个文件", len(self.video_files))
        else:
            self.tracker.finish("预览完成，未写入刮削文件")
            logger.info(
                "预览完成，共 %d 个计划（未写入刮削文件）", len(self.video_files),
            )
        return self.rename_plans

    async def _apply_streaming(self, directory, rename_cfg: dict, output_csv: Optional[str]) -> None:
        """执行模式：刮削继续并发，归档一次只做一组。"""
        self._reset_stream(directory)
        self.tracker.hold_overall(0)
        self._report_overall()
        ready_groups, scrape_jobs = self._partition_groups()
        worker = asyncio.create_task(self._commit_worker(rename_cfg))
        try:
            for group in ready_groups:
                await self._commit_queue.put(group)
            if scrape_jobs:
                await self._scrape_then_queue(scrape_jobs)
            await self._commit_queue.put(None)
            await worker
            if self._delayed_groups and not self._is_cancelled() and not self._stop_further:
                await self._commit_delayed(rename_cfg)
            elif self._delayed_groups:
                self._abandon_delayed("未处理超长文件名")
        finally:
            if not worker.done():
                self._stop_further = True
                await self._commit_queue.put(None)
                await worker
            self._mark_remaining_untouched()
            self._fill_missing_plans(rename_cfg)
            self._report_overall(self._closing_message())
            self._flush_run_logs()
            if output_csv:
                self.export_csv(output_csv)
            await close_all_sessions()
        if self._is_cancelled():
            raise asyncio.CancelledError()
        logger.info("完整流水线完成，共处理 %d 个文件", len(self.video_files))

    def _reset_stream(self, directory) -> None:
        root = self._result_directory(directory)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        self._commit_queue = asyncio.Queue()
        self._reserved_folders = set()
        self._reserved_owners = {}
        self._rollback_entries = []
        self._session_by_index = {}
        self._settled = set()
        self._delayed_groups = []
        self._finished_count = 0
        self._stop_further = False
        self._commit_guard = False
        self._rollback_path = os.path.join(root, f"rollback_{stamp}.json")
        self._session_path = os.path.join(root, f"scrape_session_{stamp}.json")
        self.rename_plans = [None] * len(self.parsed_results)

    def _result_directory(self, directory) -> str:
        if isinstance(directory, (list, tuple)):
            anchor = directory[0] if directory else "."
        else:
            anchor = directory
        if os.path.isdir(anchor):
            return anchor
        return os.path.dirname(os.path.abspath(anchor)) or "."

    def _partition_groups(self):
        members: dict[str, list[int]] = {}
        order: list[str] = []
        for index, parsed in enumerate(self.parsed_results):
            key = parsed.part_group or f"solo-{index}"
            if key not in members:
                members[key] = []
                order.append(key)
            members[key].append(index)
        ready_groups = []
        scrape_jobs = []
        for key in order:
            indexes = members[key]
            needs = [index for index in indexes if self.scraped_metadata[index] is None]
            if not needs:
                ready_groups.append(indexes)
                continue
            leader = needs[0]
            followers = [index for index in needs if index != leader]
            scrape_jobs.append((leader, followers, indexes))
        return ready_groups, scrape_jobs

    async def _scrape_then_queue(self, scrape_jobs) -> None:
        aggregator = ProviderAggregator(self.config)
        from core.page_cache import get_page_cache
        get_page_cache(self.config)
        from core.proxy import get_proxy, test_proxy
        raw_proxy = self.config.get("http", {}).get("proxy", "")
        if get_proxy(self.config):
            preflight = await test_proxy(raw_proxy, timeout=5.0)
            if not preflight.get("ok"):
                logger.error(
                    "代理连通性预检失败：%s。请在 设置-网络代理 中检查配置；"
                    "本次刮削很可能无法获取在线数据。",
                    preflight.get("message"),
                )
            else:
                logger.info("代理预检通过：%s", preflight.get("message"))

        max_workers = self.config.get("concurrency", {}).get("max_workers", 10)
        semaphore = asyncio.Semaphore(max_workers)

        async def scrape_leader(leader, followers, group):
            parsed = self.parsed_results[leader]
            async with semaphore:
                if self._is_cancelled():
                    return
                fname = os.path.basename(parsed.original_path)
                self._emit_file_event({
                    "type": "file",
                    "index": leader,
                    "status": "running",
                    "stage": 30,
                })
                try:
                    metadata = await aggregator.scrape(parsed)
                    self.scraped_metadata[leader] = metadata
                    if metadata:
                        title = metadata.title or metadata.code or ""
                        logger.info("刮削成功 [%s]: %s", fname, title[:60])
                        self._emit_file_event({
                            "type": "file",
                            "index": leader,
                            "status": "running",
                            "stage": 60,
                        })
                    else:
                        logger.warning("未获取到元数据 [%s]", fname)
                        self._emit_file_event({
                            "type": "file",
                            "index": leader,
                            "status": "skipped",
                            "stage": 0,
                        })
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.error("刮削失败 [%s]: %s", fname, exc)
                    self._emit_file_event({
                        "type": "file",
                        "index": leader,
                        "status": "failed",
                        "stage": 30,
                    })
                for follower in followers:
                    self.scraped_metadata[follower] = self.scraped_metadata[leader]
                    followed = self.scraped_metadata[follower]
                    self._emit_file_event({
                        "type": "file",
                        "index": follower,
                        "status": "running" if followed else "skipped",
                        "stage": 60 if followed else 0,
                    })
            await self._commit_queue.put(group)

        tasks = [
            scrape_leader(leader, followers, group)
            for leader, followers, group in scrape_jobs
        ]
        gather_task = asyncio.gather(*tasks)

        async def watch_cancel():
            while not self._is_cancelled():
                await asyncio.sleep(0.1)
            gather_task.cancel()

        watcher = asyncio.ensure_future(watch_cancel())
        try:
            await gather_task
        except asyncio.CancelledError:
            logger.info("刮削已中断，未开始归档的文件保持原样。")
        finally:
            watcher.cancel()

    async def _commit_worker(self, rename_cfg: dict) -> None:
        while True:
            group = await self._commit_queue.get()
            if group is None:
                return
            if self._is_cancelled() or self._stop_further:
                self._settle_indexes(group, "未改动", note="中断时尚未归档", file_status="waiting")
                self._flush_run_logs()
                continue
            self._commit_guard = True
            try:
                outcome = await asyncio.shield(self._commit_ready_group(group, rename_cfg))
            except Exception:
                logger.exception("归档失败")
                self._settle_indexes(group, "失败", note="归档异常", file_status="failed", stage=80)
                outcome = "failed"
            finally:
                self._commit_guard = False
            if outcome == "failed":
                self._stop_further = True
            self._flush_run_logs()

    async def _commit_ready_group(self, indexes: list[int], rename_cfg: dict) -> str:
        plans = self._plans_for_indexes(indexes, rename_cfg)
        strategy = rename_cfg.get("conflict_resolution", "auto_suffix")
        resolved = resolve_plan_conflicts(
            plans, strategy, self._reserved_folders, self._reserved_owners,
        )
        resolved_ids = {id(plan) for plan in resolved}
        for plan in plans:
            if id(plan) not in resolved_ids:
                index = self._row_index(plan.source_path)
                self._settle(index, "未改动", note="目标冲突已跳过", file_status="skipped", stage=60)
        for plan in resolved:
            self._claim_plan(plan)
        if any(plan.name_truncated for plan in resolved):
            self._delayed_groups.append(indexes)
            for plan in resolved:
                index = self._row_index(plan.source_path)
                if index is not None:
                    self._emit_file_event({
                        "type": "file",
                        "index": index,
                        "status": "waiting",
                        "stage": 60,
                    })
            return "parked"
        return await self._write_and_move(resolved)

    async def _write_and_move(self, plans: list) -> str:
        if not plans:
            return "ok"
        await persist_sidecars_and_posters(
            self, {plan.source_path for plan in plans},
        )
        logs = await self._apply_plans_async(
            plans, honor_cancel=False, write_rollback=False,
        )
        self._apply_log.extend(logs)
        self._rollback_entries.extend(logs)
        by_source = {}
        for entry in logs:
            source = entry.get("source")
            if source:
                by_source[source] = entry
        failed = False
        for plan in plans:
            index = self._row_index(plan.source_path)
            entry = by_source.get(plan.source_path)
            action = entry.get("action") if entry else ""
            if action == "error":
                failed = True
                self._settle(
                    index, "失败", plan.target_path, entry.get("error", ""),
                    file_status="failed", stage=80,
                )
            elif action == "skipped_no_metadata":
                self._settle(
                    index, "未改动", note="未刮削到信息", file_status="skipped", stage=0,
                )
            elif action == "move" or not os.path.exists(plan.source_path):
                self._settle(index, "已归档", plan.target_path)
            elif self._same_path(plan.source_path, plan.target_path):
                self._settle(index, "已归档", plan.target_path)
            else:
                self._settle(index, "未改动", plan.target_path, "未移动", file_status="waiting")
        return "failed" if failed else "ok"

    async def _commit_delayed(self, rename_cfg: dict) -> None:
        if not self._confirm_truncated_names():
            self._abandon_delayed("未确认超长文件名")
            return
        plans = []
        for group in self._delayed_groups:
            for index in group:
                plan = self.rename_plans[index] if index < len(self.rename_plans) else None
                if plan is not None:
                    self._release_plan(plan)
                    plans.append(plan)
        strategy = rename_cfg.get("conflict_resolution", "auto_suffix")
        resolve_plan_conflicts(
            plans, strategy, self._reserved_folders, self._reserved_owners,
        )
        for plan in plans:
            self._claim_plan(plan)
        for group in self._delayed_groups:
            if self._is_cancelled() or self._stop_further:
                self._settle_indexes(group, "未改动", note="中断时尚未归档", file_status="waiting")
                continue
            group_plans = [
                self.rename_plans[index]
                for index in group
                if index < len(self.rename_plans) and self.rename_plans[index] is not None
            ]
            self._commit_guard = True
            try:
                outcome = await asyncio.shield(self._write_and_move(group_plans))
            except Exception:
                logger.exception("超长文件归档失败")
                self._settle_indexes(group, "失败", note="归档异常", file_status="failed", stage=80)
                outcome = "failed"
            finally:
                self._commit_guard = False
            if outcome == "failed":
                self._stop_further = True
            self._flush_run_logs()

    def _abandon_delayed(self, note: str) -> None:
        for group in self._delayed_groups:
            for index in group:
                plan = self.rename_plans[index] if index < len(self.rename_plans) else None
                if plan is not None:
                    plan.apply_status = "skipped"
                self._settle(index, "未改动", note=note, file_status="skipped", stage=60)
        self._flush_run_logs()

    def _plans_for_indexes(self, indexes: list[int], rename_cfg: dict) -> list:
        templates = rename_cfg.get("templates", {})
        create_subfolder = self.config.get("metadata", {}).get("create_subfolder", True)
        output_mode = rename_cfg.get("output_mode", "in_place")
        custom_dir = rename_cfg.get("custom_output_dir", "")
        plans = []
        for index in indexes:
            parsed = self.parsed_results[index]
            metadata = self.scraped_metadata[index] if index < len(self.scraped_metadata) else None
            plan = build_rename_plan(
                parsed=parsed,
                templates=templates,
                metadata=metadata,
                create_subfolder=create_subfolder,
                output_mode=output_mode,
                custom_output_dir=custom_dir,
            )
            self.rename_plans[index] = plan
            plans.append(plan)
        return plans

    def _fill_missing_plans(self, rename_cfg: dict) -> None:
        if len(self.rename_plans) != len(self.parsed_results):
            padded = [None] * len(self.parsed_results)
            for index, plan in enumerate(self.rename_plans[:len(padded)]):
                padded[index] = plan
            self.rename_plans = padded
        missing = [index for index, plan in enumerate(self.rename_plans) if plan is None]
        if missing:
            self._plans_for_indexes(missing, rename_cfg)

    def _claim_plan(self, plan: RenamePlan) -> None:
        key = os.path.normcase(os.path.abspath(os.path.dirname(plan.target_path)))
        self._reserved_folders.add(key)
        if plan.part_group:
            self._reserved_owners[key] = plan.part_group

    def _release_plan(self, plan: RenamePlan) -> None:
        key = os.path.normcase(os.path.abspath(os.path.dirname(plan.target_path)))
        self._reserved_folders.discard(key)
        if plan.part_group and self._reserved_owners.get(key) == plan.part_group:
            self._reserved_owners.pop(key, None)

    def _settle(
        self,
        index: Optional[int],
        status: str,
        target: str = "",
        note: str = "",
        file_status: Optional[str] = None,
        stage: int = 0,
    ) -> None:
        if index is None or index in self._settled:
            return
        self._settled.add(index)
        source = self.parsed_results[index].original_path
        self._session_by_index[index] = {
            "source": source,
            "target": target,
            "status": status,
            "note": note,
        }
        self._finished_count += 1
        if file_status:
            self._emit_file_event({
                "type": "file",
                "index": index,
                "status": file_status,
                "stage": stage,
            })
        self._report_overall()

    def _settle_indexes(
        self,
        indexes: list[int],
        status: str,
        note: str = "",
        file_status: Optional[str] = None,
        stage: int = 0,
    ) -> None:
        for index in indexes:
            self._settle(index, status, note=note, file_status=file_status, stage=stage)

    def _mark_remaining_untouched(self) -> None:
        self._settle_indexes(
            list(range(len(self.parsed_results))),
            "未改动",
            note="未开始归档",
            file_status="waiting",
        )

    def _report_overall(self, message: Optional[str] = None) -> None:
        total = len(self.parsed_results)
        if total <= 0:
            return
        if message is None:
            message = f"已结束 {self._finished_count}/{total}"
        self.tracker.update(
            current=self._finished_count,
            overall=100.0 * self._finished_count / total,
            message=message,
        )

    def _closing_message(self) -> str:
        counts = {"已归档": 0, "未改动": 0, "失败": 0}
        for record in self._session_by_index.values():
            status = record.get("status")
            if status in counts:
                counts[status] += 1
        if self._is_cancelled():
            prefix = "已中断"
        elif self._stop_further:
            prefix = "已停止"
        else:
            prefix = "执行完成"
        return (
            f"{prefix}：已归档 {counts['已归档']} 个，"
            f"未改动 {counts['未改动']} 个，失败 {counts['失败']} 个"
        )

    def _flush_run_logs(self) -> None:
        if not getattr(self, "_session_path", ""):
            return
        folder = os.path.dirname(self._session_path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        files = [self._session_by_index[index] for index in sorted(self._session_by_index)]
        with open(self._session_path, "w", encoding="utf-8") as handle:
            json.dump({"files": files}, handle, ensure_ascii=False, indent=2)
        if self._rollback_entries:
            with open(self._rollback_path, "w", encoding="utf-8") as handle:
                json.dump(self._rollback_entries, handle, ensure_ascii=False, indent=2)

    @staticmethod
    def _same_path(left: str, right: str) -> bool:
        return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))

    def _build_rename_plans(self, rename_cfg: dict) -> None:
        templates = rename_cfg.get("templates", {})
        create_subfolder = self.config.get("metadata", {}).get("create_subfolder", True)
        output_mode = rename_cfg.get("output_mode", "in_place")
        custom_dir = rename_cfg.get("custom_output_dir", "")

        self.rename_plans = []
        for i, parsed in enumerate(self.parsed_results):
            metadata = self.scraped_metadata[i] if i < len(self.scraped_metadata) else None
            self.rename_plans.append(build_rename_plan(
                parsed=parsed,
                templates=templates,
                metadata=metadata,
                create_subfolder=create_subfolder,
                output_mode=output_mode,
                custom_output_dir=custom_dir,
            ))

        conflict_strategy = rename_cfg.get("conflict_resolution", "auto_suffix")
        self.rename_plans = resolve_plan_conflicts(self.rename_plans, conflict_strategy)

    def _row_index(self, source_path: str) -> Optional[int]:
        for index, parsed in enumerate(self.parsed_results):
            if parsed.original_path == source_path:
                return index
        return None

    async def _commit_ordered(self) -> None:
        """先归档文件名正常的文件，最后再统一确认并归档超长文件名。"""
        delayed = [plan for plan in self.rename_plans if plan.name_truncated]
        normal = [plan for plan in self.rename_plans if not plan.name_truncated]
        for plan in delayed:
            index = self._row_index(plan.source_path)
            if index is not None:
                self._emit_file_event({
                    "type": "file",
                    "index": index,
                    "status": "waiting",
                    "stage": 60,
                })

        if normal:
            logger.info("先处理 %d 个文件名正常的文件", len(normal))
            self.tracker.update(message=f"先处理文件名正常的文件（{len(normal)}）")
            await persist_sidecars_and_posters(
                self, {plan.source_path for plan in normal},
            )
            self._apply_log.extend(await self._apply_plans_async(normal))

        if not delayed:
            return

        logger.info("正常文件已完成，开始处理 %d 个超长文件名", len(delayed))
        self.tracker.update(
            message=f"请确认 {len(delayed)} 个超长文件名",
        )
        if not self._confirm_truncated_names():
            for plan in delayed:
                plan.apply_status = "skipped"
                index = self._row_index(plan.source_path)
                if index is not None:
                    self._emit_file_event({
                        "type": "file",
                        "index": index,
                        "status": "skipped",
                        "stage": 60,
                    })
            logger.info("已跳过超长文件，这些视频保留在原位置")
            return

        strategy = self.config.get("rename", {}).get("conflict_resolution", "auto_suffix")
        resolve_plan_conflicts(delayed, strategy)
        self.tracker.update(message=f"处理超长文件名（{len(delayed)}）")
        await persist_sidecars_and_posters(
            self, {plan.source_path for plan in delayed},
        )
        self._apply_log.extend(await self._apply_plans_async(delayed))

    def _confirm_truncated_names(self) -> bool:
        """让用户确认超长名称。没有回调时沿用自动截断。用户取消时返回 False。"""
        pending = [
            plan for plan in self.rename_plans
            if plan is not None and plan.name_truncated
        ]
        callback = self._long_name_callback
        if not pending or callback is None:
            return True
        logger.info("有 %d 个文件名超出长度限制，等待确认", len(pending))
        choices = callback(pending)
        if choices is None:
            logger.info("用户取消了超长文件名确认")
            return False
        apply_long_name_choices(
            [plan for plan in self.rename_plans if plan is not None],
            choices,
        )
        return True

    def export_csv(self, output_path: str) -> str:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        with open(output_path, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "序号", "类型", "原始路径", "目标路径",
                "标题", "年份", "番号", "季", "集", "分辨率",
                "刮削源", "评分", "演员",
            ])
            for i, plan in enumerate(self.rename_plans, 1):
                parsed = self.parsed_results[i - 1] if i - 1 < len(self.parsed_results) else None
                meta = self.scraped_metadata[i - 1] if i - 1 < len(self.scraped_metadata) else None
                actors = ", ".join(actor.name for actor in meta.actors) if meta else ""
                writer.writerow([
                    i,
                    plan.video_type.value,
                    plan.source_path,
                    plan.target_path,
                    parsed.title if parsed else "",
                    parsed.year if parsed else "",
                    parsed.code if parsed else "",
                    parsed.season if parsed else "",
                    parsed.episode if parsed else "",
                    parsed.resolution if parsed else "",
                    meta.source_provider if meta else "",
                    meta.rating if meta else "",
                    actors,
                ])

        logger.info("CSV 已导出: %s (%d 条记录)", output_path, len(self.rename_plans))
        return output_path

    def get_summary(self) -> dict:
        type_counts: Dict[str, int] = {}
        for plan in self.rename_plans:
            kind = plan.video_type.value
            type_counts[kind] = type_counts.get(kind, 0) + 1

        return {
            "total_files": len(self.video_files),
            "parsed_count": len(self.parsed_results),
            "scraped_count": sum(1 for meta in self.scraped_metadata if meta is not None),
            "rename_plans": len(self.rename_plans),
            "type_distribution": type_counts,
        }
