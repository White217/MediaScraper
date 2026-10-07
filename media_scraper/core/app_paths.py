"""Frozen / 开发环境下的路径解析。"""

from __future__ import annotations

import os
import sys


def _project_root() -> str:
    """源码树中 media_scraper 目录。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _exe_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return _project_root()


def _is_writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write_probe")
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def writable_root() -> str:
    """可写数据目录：优先 EXE/项目旁，不可写时回退到用户目录。"""
    primary = _exe_dir()
    if _is_writable(primary):
        return primary
    fallback = os.path.join(os.path.expanduser("~"), "MediaScraper")
    os.makedirs(fallback, exist_ok=True)
    return fallback


def resource_path(relative: str) -> str:
    """只读资源（config 出厂副本、QSS、图标等）。"""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", _exe_dir())
    else:
        base = _project_root()
    return os.path.join(base, relative)


def config_file_path() -> str:
    return os.path.join(writable_root(), "config.yaml")


def data_path(name: str) -> str:
    return os.path.join(writable_root(), name)
