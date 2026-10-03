"""
流水线编排模块
完整流水线: 扫描 → 解析 → 刮削 → 元数据写入 → 重命名计划
"""

import asyncio
import csv
import logging
import os
import time
from typing import Callable, Dict, List, Optional

from core.config import load_config
from core.http_sessions import close_all_sessions
from core.models import Metadata, ParsedFilename, ProgressInfo, RenamePlan
from core.parser import parse_filename
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
from rename.renamer import build_rename_plan, resolve_plan_conflicts

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

        self._progress_callback: Optional[Callable[[ProgressInfo], None]] = None
        self._file_event_callback: Optional[Callable[[dict], None]] = None
        self._cancel_event = None

    def set_progress_callback(self, callback: Callable[[ProgressInfo], None]) -> None:
        self._progress_callback = callback
        self.tracker.set_callback(callback)

    def set_file_event_callback(self, callback: Callable[[dict], None]) -> None:
        self._file_event_callback = callback

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
        await self._apply_plans_async(self.rename_plans)
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
        else:
            plans = self._run_scan_only(directory, output_csv)

        if not plans:
            logger.info("无重命名计划，跳过执行")
            return []
        return self._apply_plans(plans)

    def _apply_plans(self, plans: List[RenamePlan]) -> List[dict]:
        return asyncio.run(self._apply_plans_async(plans))

    async def _apply_plans_async(self, plans: List[RenamePlan]) -> List[dict]:
        return await apply_plans_async(self, plans)

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
        logger.info("解析完成: %d 个文件", len(self.parsed_results))
        return True

    def _run_scan_only(
        self, directory: str, output_csv: Optional[str] = None
    ) -> List[RenamePlan]:
        rename_cfg = self.config.get("rename", {})
        self._scan_and_parse(directory)

        self.scraped_metadata = [None] * len(self.parsed_results)
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

        self.tracker.start_phase("searching")
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

        self.scraped_metadata = [None] * len(self.parsed_results)
        concurrency_cfg = self.config.get("concurrency", {})
        max_workers = concurrency_cfg.get("max_workers", 10)
        semaphore = asyncio.Semaphore(max_workers)
        processed_count = 0

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
                    processed_count += 1
                    self.tracker.update(
                        current=processed_count,
                        message=f"搜索中 ({processed_count}/{self.tracker.total_files})",
                        file_name=fname,
                    )

        await self._check_cancel()
        tasks = [scrape_with_limit(i, parsed) for i, parsed in enumerate(self.parsed_results)]
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
            await persist_sidecars_and_posters(self)

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
