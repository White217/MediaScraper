"""文件名清理与冲突处理。"""

from __future__ import annotations

import os
import re
import unicodedata

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str) -> str:
    if not name:
        return "untitled"
    cleaned = _INVALID.sub("", name)
    cleaned = "".join(ch for ch in cleaned if unicodedata.category(ch)[0] != "C")
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(".")
    return cleaned or "untitled"


def resolve_conflict(path: str, strategy: str = "auto_suffix") -> str:
    if not os.path.exists(path):
        return path
    if strategy == "skip":
        return ""
    base, ext = os.path.splitext(path)
    counter = 1
    candidate = f"{base} ({counter}){ext}"
    while os.path.exists(candidate) and counter < 1000:
        counter += 1
        candidate = f"{base} ({counter}){ext}"
    return candidate
