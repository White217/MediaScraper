"""执行重命名、配套文件复制与回滚。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from core.models import RenamePlan
from metadata.json_writer import write_json
from metadata.nfo import write_nfo

logger = logging.getLogger(__name__)

COMPANION_EXTENSIONS = {
    "srt", "ass", "ssa", "vtt", "sub", "idx", "sup", "smi", "lrc",
    "nfo", "txt", "jpg", "png",
}
POSTER_SUFFIXES = ("-poster.jpg", "-poster.png", "poster.jpg", "poster.png")


def list_companion_files(src_video: str) -> list[str]:
    """在移动视频之前记下同目录、同名前缀的字幕和配套文件。"""
    src_dir = os.path.dirname(src_video)
    video_stem = os.path.splitext(os.path.basename(src_video))[0]
    found: list[str] = []
    if not video_stem or not os.path.isdir(src_dir):
        return found
    try:
        names = os.listdir(src_dir)
    except OSError as exc:
        logger.warning("读取配套文件失败：%s", exc)
        return found
    for fname in names:
        ext = os.path.splitext(fname)[1].lower().lstrip(".")
        if ext not in COMPANION_EXTENSIONS:
            continue
        if not fname.startswith(video_stem):
            continue
        if any(fname.endswith(suffix) for suffix in POSTER_SUFFIXES):
            continue
        src_path = os.path.join(src_dir, fname)
        if os.path.isfile(src_path):
            found.append(src_path)
    return found


def copy_companion_files(
    src_video: str,
    dst_dir: str,
    sources: Optional[list[str]] = None,
) -> list:
    """把配套文件移到目标文件夹。sources 应在视频移动前采集。"""
    paths = list_companion_files(src_video) if sources is None else list(sources)
    moved = []
    os.makedirs(dst_dir, exist_ok=True)
    for src_path in paths:
        fname = os.path.basename(src_path)
        dst_path = os.path.join(dst_dir, fname)
        if os.path.normcase(os.path.abspath(src_path)) == os.path.normcase(os.path.abspath(dst_path)):
            continue
        if not os.path.isfile(src_path):
            continue
        if os.path.exists(dst_path):
            logger.info("目标已存在，跳过配套文件：%s", fname)
            continue
        try:
            shutil.move(src_path, dst_path)
        except OSError as exc:
            logger.warning("移动配套文件失败（不阻断主流程）：%s", exc)
            continue
        moved.append(fname)
        logger.info("已移动配套文件：%s", fname)
    return moved


def ensure_metadata_in_folder(folder: str, plan: RenamePlan) -> bool:
    """目标文件夹里没有 NFO/JSON 时按计划补写。写失败则返回 False。"""
    meta = plan.metadata
    if meta is None:
        return False
    out = Path(folder)
    ok = True
    nfo_path = out / "movie.nfo"
    json_path = out / "metadata.json"
    if not nfo_path.is_file():
        ok = write_nfo(meta, out, "movie.nfo") is not None and ok
    if not json_path.is_file():
        ok = write_json(meta, out, "metadata.json") is not None and ok
    return ok and nfo_path.is_file() and json_path.is_file()


def save_rollback_log(log: List[dict], directory: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(directory, f"rollback_{timestamp}.json")
    with open(log_path, "w", encoding="utf-8") as handle:
        json.dump(log, handle, ensure_ascii=False, indent=2)
    logger.info("回滚日志已保存: %s", log_path)
    return log_path


def rollback(log_path: str) -> List[dict]:
    """从回滚日志恢复文件。"""
    with open(log_path, "r", encoding="utf-8") as handle:
        log = json.load(handle)

    results = []
    for entry in reversed(log):
        if entry["action"] != "move":
            continue
        target = entry["target"]
        source = entry["source"]

        if os.path.exists(target):
            try:
                shutil.move(target, source)
                results.append({
                    "action": "rollback",
                    "from": target,
                    "to": source,
                    "status": "ok",
                })
                logger.info(
                    "已回滚: %s -> %s",
                    os.path.basename(target),
                    os.path.basename(source),
                )
            except Exception as exc:
                results.append({
                    "action": "rollback",
                    "from": target,
                    "to": source,
                    "status": "error",
                    "error": str(exc),
                })
        else:
            results.append({
                "action": "skip",
                "reason": "target_not_found",
                "target": target,
            })
    return results


async def apply_plans_async(pipeline, plans: List[RenamePlan]) -> List[dict]:
    """实际执行重命名和文件移动；每个文件前检查中断标志。"""
    pipeline.tracker.start_phase("apply")
    rollback_log: List[dict] = []
    total = len(plans)
    if total == 0:
        pipeline.tracker.finish("没有需要移动的文件")
        return []

    def _index_of(path):
        for index, parsed in enumerate(pipeline.parsed_results):
            if parsed.original_path == path:
                return index
        return None

    moved_count = 0
    skipped_no_meta = 0
    for i, plan in enumerate(plans):
        await pipeline._check_cancel()
        src = plan.source_path
        dst = plan.target_path
        fname = os.path.basename(src)

        pipeline.tracker.update(
            current=i,
            ratio=i / total,
            message=f"移动: {fname}",
            file_name=fname,
        )

        if plan.metadata is None:
            skipped_no_meta += 1
            logger.warning("未刮削到信息，已保留在原位置（未移动）: %s", fname)
            rollback_log.append({
                "action": "skipped_no_metadata",
                "source": src,
                "timestamp": datetime.now().isoformat(),
            })
            pipeline.tracker.update(
                current=i + 1,
                ratio=(i + 1) / total,
                message=f"已跳过: {fname}",
                file_name=fname,
            )
            continue

        dst_dir = os.path.dirname(dst)
        os.makedirs(dst_dir, exist_ok=True)
        companions = list_companion_files(src)

        if os.path.exists(dst):
            logger.warning("目标已存在，跳过: %s", dst)
            pipeline.tracker.update(
                current=i + 1,
                ratio=(i + 1) / total,
                message=f"已跳过: {fname}",
                file_name=fname,
            )
            continue

        if not ensure_metadata_in_folder(dst_dir, plan):
            logger.error("元数据未能写入，视频保留在原位置: %s", fname)
            row_index = _index_of(src)
            if row_index is not None:
                pipeline._emit_file_event({
                    "type": "file",
                    "index": row_index,
                    "status": "failed",
                    "stage": 80,
                })
            rollback_log.append({
                "action": "error",
                "source": src,
                "target": dst,
                "error": "metadata write failed",
                "timestamp": datetime.now().isoformat(),
            })
            pipeline.tracker.update(
                current=i + 1,
                ratio=(i + 1) / total,
                message=f"元数据写入失败: {fname}",
                file_name=fname,
            )
            continue

        try:
            await asyncio.to_thread(shutil.move, src, dst)
            await asyncio.sleep(0)
            moved_count += 1
            logger.info("已移动: %s -> %s", fname, os.path.basename(dst))
            companion_copied = copy_companion_files(src, dst_dir, companions)
            if companion_copied:
                logger.info("已移动 %d 个配套文件：%s", len(companion_copied), companion_copied)
            row_index = _index_of(src)
            if row_index is not None:
                pipeline._emit_file_event({
                    "type": "file",
                    "index": row_index,
                    "status": "success",
                    "stage": 100,
                })

            rollback_log.append({
                "action": "move",
                "source": src,
                "target": dst,
                "timestamp": datetime.now().isoformat(),
            })

        except Exception as exc:
            logger.error("移动失败 [%s]: %s", fname, exc)
            row_index = _index_of(src)
            if row_index is not None:
                pipeline._emit_file_event({
                    "type": "file",
                    "index": row_index,
                    "status": "failed",
                    "stage": 80,
                })
            rollback_log.append({
                "action": "error",
                "source": src,
                "target": dst,
                "error": str(exc),
                "timestamp": datetime.now().isoformat(),
            })
        finally:
            pipeline.tracker.update(
                current=i + 1,
                ratio=(i + 1) / total,
                message=f"已处理: {fname}",
                file_name=fname,
            )

    if rollback_log:
        save_rollback_log(rollback_log, os.path.dirname(plans[0].source_path))

    if skipped_no_meta:
        logger.warning(
            "共有 %d 个视频因未刮削到信息而保留在原位置，成功归档 %d 个。请检查网络/代理后重试。",
            skipped_no_meta,
            moved_count,
        )
    pipeline.tracker.update(
        current=total,
        message=f"执行完成: 已归档 {moved_count} 个，{skipped_no_meta} 个因无信息保留原位",
    )
    return rollback_log
