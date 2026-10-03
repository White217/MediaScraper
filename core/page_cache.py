"""HTML 页面磁盘缓存。"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

_CACHE: Optional["PageCache"] = None


class PageCache:
    def __init__(
        self,
        cache_dir: os.PathLike | str,
        ttl: int = 86400,
        max_entries: int = 500,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.pages_dir = self.cache_dir / "pages"
        self.pages_dir.mkdir(parents=True, exist_ok=True)
        self.ttl = int(ttl)
        self.max_entries = max(1, int(max_entries))
        self._writes = 0

    def _path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        safe = quote(url, safe="")[:80]
        return self.pages_dir / f"{digest}_{safe}.html"

    def get(self, url: str) -> Optional[str]:
        if self.ttl <= 0:
            return None
        path = self._path(url)
        if not path.is_file():
            return None
        age = time.time() - path.stat().st_mtime
        if age > self.ttl:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return None
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def set(self, url: str, body: str) -> None:
        if self.ttl <= 0 or not body:
            return
        path = self._path(url)
        try:
            path.write_text(body, encoding="utf-8")
        except OSError:
            return
        self._writes += 1
        if self._writes % 50 == 0:
            self._evict_if_needed()

    def _evict_if_needed(self) -> None:
        files = sorted(
            self.pages_dir.glob("*.html"),
            key=lambda p: p.stat().st_mtime,
        )
        overflow = len(files) - self.max_entries
        for path in files[: max(0, overflow)]:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def get_page_cache(config: Optional[dict[str, Any]] = None) -> PageCache:
    global _CACHE
    if _CACHE is not None and config is None:
        return _CACHE

    http = (config or {}).get("http", {}) if config else {}
    ttl = int(http.get("cache_ttl", 86400))
    if http.get("cache_enabled") is False:
        ttl = 0
    rel = http.get("cache_dir", "cache")
    from core.app_paths import writable_root

    cache_root = rel if os.path.isabs(rel) else os.path.join(writable_root(), rel)
    _CACHE = PageCache(cache_dir=cache_root, ttl=ttl)
    return _CACHE
