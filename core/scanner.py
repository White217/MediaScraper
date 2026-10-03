"""
目录扫描模块
Scan directories for video files, applying extension filters and exclusion rules.
"""

import logging
import os
from pathlib import Path
from typing import Optional

from core.parser import VIDEO_EXTENSIONS

logger = logging.getLogger(__name__)


def _meets_min_size(path: str, min_bytes: int) -> bool:
    if min_bytes <= 0:
        return True
    try:
        return os.path.getsize(path) >= min_bytes
    except OSError:
        return False


def scan_directory(
    directory: str,
    video_extensions: Optional[set[str]] = None,
    scan_subdirs: bool = True,
    exclude_dirs: Optional[set[str]] = None,
    min_file_size_mb: float = 0,
) -> list[str]:
    """扫描目录，返回视频文件路径列表。

    Args:
        directory:        要扫描的根目录路径。
        video_extensions: 有效视频扩展名集合（含点号，小写），默认使用全局列表。
        scan_subdirs:     是否递归扫描子目录。
        exclude_dirs:     需要排除的目录名集合（精确匹配，不区分大小写）。

    Returns:
        排序后的视频文件绝对路径列表。

    Raises:
        NotADirectoryError: directory 不存在或不是目录。
    """
    root = Path(directory).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"目录不存在: {directory}")

    exts = video_extensions or VIDEO_EXTENSIONS
    excluded = {d.lower() for d in (exclude_dirs or set())}
    min_bytes = int(max(0.0, float(min_file_size_mb or 0)) * 1024 * 1024)

    results: list[str] = []

    if scan_subdirs:
        for dirpath, dirnames, filenames in os.walk(root):
            # 过滤排除目录（原地修改 dirnames 可阻止 os.walk 进入）
            dirnames[:] = [
                d for d in dirnames
                if d.lower() not in excluded
            ]
            for fname in filenames:
                ext = os.path.splitext(fname)[1].lower()
                if ext in exts:
                    full = os.path.join(dirpath, fname)
                    if _meets_min_size(full, min_bytes):
                        results.append(full)
    else:
        for fname in os.listdir(root):
            fpath = os.path.join(root, fname)
            if os.path.isfile(fpath):
                ext = os.path.splitext(fname)[1].lower()
                if ext in exts and _meets_min_size(fpath, min_bytes):
                    results.append(fpath)

    results.sort()
    logger.info("扫描完成: %s，共发现 %d 个视频文件", directory, len(results))
    return results


def collect_videos(
    paths: list[str],
    video_extensions: Optional[set[str]] = None,
    scan_subdirs: bool = True,
    exclude_dirs: Optional[set[str]] = None,
    min_file_size_mb: float = 0,
) -> list[str]:
    """把选中的文件和文件夹收成去重后的视频列表。

    文件夹按 scan_directory 展开。视频文件只收录自身。
    字幕、种子等非视频文件忽略。同一路径只保留一次。
    """
    exts = {ext.lower() for ext in (video_extensions or VIDEO_EXTENSIONS)}
    seen: set[str] = set()
    results: list[str] = []

    for raw in paths:
        path = os.path.abspath(raw)
        if os.path.isdir(path):
            found = scan_directory(
                path,
                video_extensions=exts,
                scan_subdirs=scan_subdirs,
                exclude_dirs=exclude_dirs,
                min_file_size_mb=min_file_size_mb,
            )
        elif os.path.isfile(path):
            ext = os.path.splitext(path)[1].lower()
            min_bytes = int(max(0.0, float(min_file_size_mb or 0)) * 1024 * 1024)
            found = [path] if ext in exts and _meets_min_size(path, min_bytes) else []
        else:
            logger.warning("路径不存在，已跳过: %s", raw)
            continue
        for item in found:
            key = os.path.normcase(os.path.abspath(item))
            if key in seen:
                continue
            seen.add(key)
            results.append(os.path.abspath(item))

    results.sort()
    return results
