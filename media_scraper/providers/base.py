"""刮削源基类。"""

from __future__ import annotations

import contextlib
import logging
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, List, Optional

import aiohttp

from core.http_sessions import DEFAULT_USER_AGENT
from core.models import Metadata, ParsedFilename, SearchResult

logger = logging.getLogger(__name__)


class BaseProvider(ABC):
    def __init__(
        self,
        proxy: str = "",
        connector: Any = None,
        request_delay: float = 0.3,
        request_jitter: float = 0.6,
        **kwargs: Any,
    ) -> None:
        del kwargs
        self._proxy = proxy or ""
        self._connector = connector
        self.request_delay = float(request_delay)
        self.request_jitter = float(request_jitter)

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @property
    def display_name(self) -> str:
        return self.name

    @property
    def supported_types(self) -> List[str]:
        return ["movie", "episode", "coded"]

    def can_handle(self, parsed: ParsedFilename) -> bool:
        return parsed.video_type.value in self.supported_types

    def request_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if self._proxy and self._connector is None:
            kwargs["proxy"] = self._proxy
        return kwargs

    @contextlib.asynccontextmanager
    async def make_session(self, timeout: int = 30) -> AsyncIterator[aiohttp.ClientSession]:
        client_timeout = aiohttp.ClientTimeout(total=timeout)
        headers = {"User-Agent": DEFAULT_USER_AGENT}
        if self._connector is not None:
            session = aiohttp.ClientSession(
                timeout=client_timeout,
                headers=headers,
                connector=self._connector,
            )
            try:
                yield session
            finally:
                if not session.closed:
                    await session.close()
            return

        from core.http_sessions import get_session_pool

        session = await get_session_pool().get(
            timeout=timeout,
            user_agent=DEFAULT_USER_AGENT,
            proxy=self._proxy or None,
            connector=None,
        )
        yield session

    @abstractmethod
    async def search(self, query: str, parsed: ParsedFilename) -> List[SearchResult]:
        raise NotImplementedError

    async def get_detail(self, result: SearchResult) -> Optional[Metadata]:
        return None

    async def scrape(self, parsed: ParsedFilename) -> Optional[Metadata]:
        if not self.can_handle(parsed):
            return None
        query = getattr(self, "_build_query", lambda p: p.code or p.title or "")(parsed)
        if not query:
            return None
        results = await self.search(query, parsed)
        if not results:
            return None
        best = results[0]
        if parsed.code:
            for item in results:
                r_code = (item.extra or {}).get("code") if item.extra else None
                if not r_code and item.metadata is not None:
                    r_code = item.metadata.code
                if r_code and str(r_code).upper() == parsed.code.upper():
                    best = item
                    break
        if getattr(best, "metadata", None) is not None:
            return best.metadata
        detail = await self.get_detail(best)
        return detail
