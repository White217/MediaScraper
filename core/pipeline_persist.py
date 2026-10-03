"""写入 NFO/JSON 并下载封面。不改变预览/执行分界。"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path

from metadata import write_json, write_nfo
from metadata.image import create_placeholder, is_local_placeholder

logger = logging.getLogger(__name__)


async def persist_sidecars_and_posters(pipeline) -> None:
    """仅在执行刮削时调用：写元数据文件，再批量下封面。"""
    meta_cfg = pipeline.config.get("metadata", {})
    rename_cfg = pipeline.config.get("rename", {})
    output_mode = meta_cfg.get("output_mode", "alongside")
    custom_output_dir = rename_cfg.get("custom_output_dir", "")
    write_nfo_flag = meta_cfg.get("nfo", {}).get("enabled", True)
    write_json_flag = meta_cfg.get("json", {}).get("enabled", True)
    create_subfolder = meta_cfg.get("create_subfolder", True)
    poster_jobs = []

    pipeline.tracker.start_phase("metadata")
    for i, (parsed, metadata) in enumerate(
        zip(pipeline.parsed_results, pipeline.scraped_metadata)
    ):
        fname = os.path.basename(parsed.original_path)
        pipeline.tracker.update(
            current=i + 1,
            message=f"元数据: {fname}",
            file_name=fname,
        )
        if metadata is None:
            continue

        plan = pipeline.rename_plans[i] if i < len(pipeline.rename_plans) else None
        if plan:
            out_dir = Path(plan.target_path).parent
        elif output_mode == "custom_dir" and custom_output_dir:
            out_dir = Path(custom_output_dir)
        else:
            out_dir = Path(parsed.original_path).parent

        if write_nfo_flag:
            nfo_name = f"{metadata.title}.nfo" if not create_subfolder else "movie.nfo"
            season = parsed.season if parsed.season is not None else -1
            episode = parsed.episode if parsed.episode is not None else -1
            write_nfo(metadata, out_dir, nfo_name, season, episode)

        if write_json_flag:
            write_json(metadata, out_dir, "metadata.json")

        if create_subfolder:
            existing_posters = [
                name for name in os.listdir(out_dir)
                if name.lower().endswith("-poster.jpg") or name.lower() == "poster.jpg"
            ] if os.path.isdir(out_dir) else []
            real_posters = [
                name for name in existing_posters
                if not is_local_placeholder(out_dir / name)
            ]
            if real_posters:
                continue
            code = metadata.code or ""
            if code:
                poster_name = f"{code}-poster.jpg"
            else:
                safe_title = re.sub(r'[\\/:*?"<>|]', "", metadata.title)[:50].strip()
                poster_name = f"{safe_title}-poster.jpg" if safe_title else "poster.jpg"
            poster_path = out_dir / poster_name
            if metadata.poster_url:
                referer = (
                    (metadata.extra or {}).get("poster_referer")
                    or metadata.source_url
                    or "https://www.javbus.com/"
                )
                poster_jobs.append((poster_path, metadata.poster_url, metadata.title, referer))
            else:
                create_placeholder(poster_path, text=metadata.title)

    pipeline.tracker.start_phase("image")
    if poster_jobs:
        from metadata.image import download_image
        from core.proxy import create_http_client

        img_client = create_http_client(pipeline.config)
        sem = asyncio.Semaphore(6)
        done_posters = 0
        total_posters = len(poster_jobs)

        async def _one(job):
            nonlocal done_posters
            path, url, title, referer = job
            await pipeline._check_cancel()
            async with sem:
                ok = await download_image(url, path, img_client, referer=referer)
            if not ok:
                logger.warning("没有可用封面，创建占位图: %s", path.name)
                create_placeholder(path, text=title)
            done_posters += 1
            pipeline.tracker.update(
                current=done_posters,
                ratio=done_posters / total_posters,
                message=f"封面 ({done_posters}/{total_posters})",
                file_name=path.name,
            )

        try:
            await asyncio.gather(*[_one(job) for job in poster_jobs])
        finally:
            await img_client.close()
    else:
        pipeline.tracker.update(
            current=len(pipeline.video_files),
            ratio=1.0,
            message="无封面需要下载",
        )

    for i, metadata in enumerate(pipeline.scraped_metadata):
        if metadata is not None:
            pipeline._emit_file_event({
                "type": "file",
                "index": i,
                "status": "running",
                "stage": 80,
            })
