"""控制台 + 滚动文件日志。"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from core.app_paths import writable_root


def setup_logging(level: str = "INFO", log_to_file: bool = True) -> None:
    root = logging.getLogger()
    if root.handlers:
        return

    numeric = getattr(logging, str(level).upper(), logging.INFO)
    root.setLevel(numeric)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)
    root.addHandler(console)

    if log_to_file:
        log_dir = os.path.join(writable_root(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "media_scraper.log")
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=2 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
