"""
HTTP 客户端
内置限速、缓存、指数退避重试、User-Agent、robots.txt 遵守
"""

import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.robotparser import RobotFileParser

import aiohttp

logger = logging.getLogger(__name__)

# 默认 User-Agent
DEFAULT_UA = (
    "MediaScraper/0.1 (+https://github.com/example/media-scraper) "
    "Python/aiohttp"
)


class HTTPClient:
    """
    带限速/缓存/重试的异步 HTTP 客户端

    - 限速: 两次请求之间最小间隔
    - 缓存: 基于 URL 的磁盘 JSON 缓存
    - 重试: 指数退避
    - robots.txt: 首次访问域名前检查
    """

    def __init__(
        self,
        request_delay: float = 1.0,
        timeout: int = 30,
        max_retries: int = 3,
        backoff_factor: float = 2.0,
        cache_dir: Optional[str] = None,
        user_agent: str = DEFAULT_UA,
        proxy: Optional[str] = None,
        connector: Optional[Any] = None,
    ):
        self._request_delay = request_delay
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._max_retries = max_retries
        self._backoff_factor = backoff_factor
        self._user_agent = user_agent
        self._proxy = proxy
        self._connector = connector
        self._last_request_time: float = 0.0
        self._robots_cache: Dict[str, RobotFileParser] = {}
        self._session: Optional[aiohttp.ClientSession] = None

        # 磁盘缓存
        self._cache_dir = Path(cache_dir) if cache_dir else None
        if self._cache_dir:
            self._cache_dir.mkdir(parents=True, exist_ok=True)

    async def _get_session(self) -> aiohttp.ClientSession:
        # 测试可注入 mock session；正式路径复用全站共享连接池
        if self._session is not None:
            if getattr(self._session, "closed", False) is not True:
                return self._session
        from core.http_sessions import get_session_pool

        timeout = getattr(self._timeout, "total", 30) or 30
        return await get_session_pool().get(
            timeout=timeout,
            user_agent=self._user_agent,
            proxy=self._proxy,
            connector=self._connector,
        )

    @property
    def proxy(self) -> Optional[str]:
        return self._proxy

    async def close(self) -> None:
        # 共享连接池由 close_all_sessions() 统一关闭，这里只关掉测试注入的私有会话
        if self._session and not getattr(self._session, "closed", True):
            await self._session.close()
        self._session = None

    # ── 限速 ──

    async def _rate_limit(self) -> None:
        """确保两次请求之间有最小间隔"""
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self._request_delay:
            await asyncio.sleep(self._request_delay - elapsed)
        self._last_request_time = time.monotonic()

    # ── 缓存 ──

    def _cache_key(self, url: str) -> str:
        return hashlib.md5(url.encode()).hexdigest()

    def _get_cached(self, url: str) -> Optional[Any]:
        if not self._cache_dir:
            return None
        cache_file = self._cache_dir / f"{self._cache_key(url)}.json"
        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                logger.debug(f"[HTTP] 缓存命中: {url}")
                return data
            except Exception:
                return None
        return None

    def _set_cached(self, url: str, data: Any) -> None:
        if not self._cache_dir:
            return
        cache_file = self._cache_dir / f"{self._cache_key(url)}.json"
        try:
            cache_file.write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"[HTTP] 缓存写入失败: {e}")

    # ── robots.txt ──

    async def _check_robots(self, url: str) -> bool:
        """检查 robots.txt 是否允许抓取该 URL"""
        from urllib.parse import urlparse

        parsed = urlparse(url)
        domain = f"{parsed.scheme}://{parsed.netloc}"

        if domain not in self._robots_cache:
            rp = RobotFileParser()
            robots_url = f"{domain}/robots.txt"
            try:
                session = await self._get_session()
                async with session.get(robots_url) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        rp.parse(text.splitlines())
                    else:
                        # robots.txt 不存在 → 允许全部
                        rp.allow_all = True  # type: ignore
            except Exception:
                # 无法获取 robots.txt → 保守允许
                rp.allow_all = True  # type: ignore
            self._robots_cache[domain] = rp

        rp = self._robots_cache[domain]
        allowed = rp.can_fetch(self._user_agent, url)
        if not allowed:
            logger.warning(f"[HTTP] robots.txt 禁止抓取: {url}")
        return allowed

    # ── 重试 ──

    async def _request_with_retry(
        self, method: str, url: str, **kwargs: Any
    ) -> Optional[aiohttp.ClientResponse]:
        """带指数退避重试的 HTTP 请求"""
        # 自动注入代理
        if self._proxy and "proxy" not in kwargs:
            kwargs["proxy"] = self._proxy
        
        for attempt in range(self._max_retries + 1):
            try:
                session = await self._get_session()
                from core.http_sessions import respect_rate_limit

                await respect_rate_limit(url, self._request_delay, 0.0)
                resp = await session.request(method, url, **kwargs)

                if resp.status == 429:
                    # Too Many Requests → 等待更长时间
                    retry_after = int(resp.headers.get("Retry-After", "5"))
                    logger.warning(
                        f"[HTTP] 429 限速，等待 {retry_after}s: {url}"
                    )
                    await asyncio.sleep(retry_after)
                    continue

                if resp.status >= 500 and attempt < self._max_retries:
                    wait = self._backoff_factor ** attempt
                    logger.warning(
                        f"[HTTP] {resp.status} 重试 ({attempt+1}/{self._max_retries})，"
                        f"等待 {wait:.1f}s: {url}"
                    )
                    await asyncio.sleep(wait)
                    continue

                return resp

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt < self._max_retries:
                    wait = self._backoff_factor ** attempt
                    logger.warning(
                        f"[HTTP] 请求异常: {e}，重试 ({attempt+1}/{self._max_retries})，"
                        f"等待 {wait:.1f}s"
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"[HTTP] 请求最终失败: {url} - {e}")
                    return None

        return None

    # ── 公共接口 ──

    async def get_json(
        self, url: str, params: Optional[Dict] = None, use_cache: bool = True
    ) -> Optional[Dict]:
        """GET 请求并解析 JSON（带缓存）"""
        full_url = url
        if params:
            from urllib.parse import urlencode
            full_url = f"{url}?{urlencode(params)}"

        # 检查缓存
        if use_cache:
            cached = self._get_cached(full_url)
            if cached is not None:
                return cached

        # robots.txt 检查
        if not await self._check_robots(full_url):
            return None

        resp = await self._request_with_retry("GET", full_url)
        if resp is None:
            return None

        try:
            if resp.status == 200:
                data = await resp.json()
                if use_cache:
                    self._set_cached(full_url, data)
                return data
            else:
                logger.warning(f"[HTTP] {resp.status}: {full_url}")
                return None
        except Exception as e:
            logger.error(f"[HTTP] JSON 解析失败: {full_url} - {e}")
            return None

    async def download_file(
        self, url: str, dest_path: Path, headers: Optional[Dict[str, str]] = None
    ) -> bool:
        """下载文件到本地"""
        if not await self._check_robots(url):
            return False

        kwargs = {}
        if headers:
            kwargs["headers"] = headers
        
        resp = await self._request_with_retry("GET", url, **kwargs)
        if resp is None or resp.status != 200:
            return False

        try:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            content = await resp.read()
            dest_path.write_bytes(content)
            logger.info(f"[HTTP] 下载完成: {dest_path.name}")
            return True
        except Exception as e:
            logger.error(f"[HTTP] 下载失败: {url} - {e}")
            return False
