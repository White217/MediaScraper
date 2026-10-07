"""全站共享 aiohttp 会话与按域名限速。

GUI 每次任务都会新建事件循环。Lock 和 ClientSession 必须在当前循环里创建，
不能在 import 时创建，否则会报 “bound to a different event loop”。
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Optional
from urllib.parse import urlparse

import aiohttp

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,ja;q=0.7",
}


class _DomainRateLimiter:
    def __init__(self) -> None:
        self._last: dict[str, float] = {}
        # 必须在正在运行的循环里创建，才能绑到这个循环。
        self._lock = asyncio.Lock()

    async def wait(self, url: str, min_interval: float, jitter: float) -> None:
        domain = urlparse(url).netloc or url
        extra = random.uniform(0.0, max(0.0, jitter)) if jitter else 0.0
        delay = max(0.0, min_interval) + extra
        async with self._lock:
            now = time.monotonic()
            last = self._last.get(domain, 0.0)
            wait_for = last + delay - now
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            self._last[domain] = time.monotonic()


class _SessionPool:
    def __init__(self) -> None:
        self._sessions: dict[tuple, aiohttp.ClientSession] = {}
        self._lock = asyncio.Lock()

    async def get(
        self,
        *,
        timeout: float = 30,
        user_agent: Optional[str] = None,
        proxy: Optional[str] = None,
        connector: Any = None,
    ) -> aiohttp.ClientSession:
        ua = user_agent or DEFAULT_USER_AGENT
        key = (float(timeout), ua, proxy or "", id(connector))
        async with self._lock:
            session = self._sessions.get(key)
            if session is not None and not session.closed:
                return session
            headers = dict(DEFAULT_HEADERS)
            headers["User-Agent"] = ua
            session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=timeout),
                headers=headers,
                connector=connector,
            )
            self._sessions[key] = session
            return session

    async def close_all(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            if not session.closed:
                await session.close()


_loop_id: Optional[int] = None
_limiter: Optional[_DomainRateLimiter] = None
_pool: Optional[_SessionPool] = None


def reset_async_resources() -> None:
    """丢掉上一轮循环上的锁和会话引用。会话应先 close_all_sessions()。"""
    global _loop_id, _limiter, _pool
    _loop_id = None
    _limiter = None
    _pool = None


def _ensure_for_running_loop() -> tuple[_DomainRateLimiter, _SessionPool]:
    global _loop_id, _limiter, _pool
    loop = asyncio.get_running_loop()
    if _limiter is None or _pool is None or _loop_id != id(loop):
        _loop_id = id(loop)
        _limiter = _DomainRateLimiter()
        _pool = _SessionPool()
    return _limiter, _pool


def get_session_pool() -> _SessionPool:
    _, pool = _ensure_for_running_loop()
    return pool


async def respect_rate_limit(url: str, min_interval: float, jitter: float) -> None:
    limiter, _pool_obj = _ensure_for_running_loop()
    await limiter.wait(url, min_interval, jitter)


async def close_all_sessions() -> None:
    if _pool is None:
        return
    await _pool.close_all()
