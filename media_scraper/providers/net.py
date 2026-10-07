"""刮削源共用的页面获取：共享会话、每域名限速、GET 磁盘缓存。"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional
from urllib.parse import urlencode

logger = logging.getLogger(__name__)


def _cache_key(
    url: str,
    *,
    params: Optional[dict] = None,
    data: Optional[dict] = None,
    json_body: Optional[dict] = None,
) -> str:
    if params:
        url = url + "?" + urlencode(params, doseq=True)
    if data is not None:
        url = url + "?" + urlencode(data)
    if json_body is not None:
        url = url + "?" + urlencode({str(k): str(v) for k, v in json_body.items()})
    return url


async def fetch_text(
    provider,
    url: str,
    *,
    data: Optional[dict] = None,
    json_body: Optional[dict] = None,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
    use_cache: bool = True,
    timeout: int = 30,
) -> tuple[Optional[str], str]:
    """返回 (正文, 最终 URL)。非 200 或网络错误时正文为 None。"""
    from core.http_sessions import respect_rate_limit
    from core.page_cache import get_page_cache

    cache = get_page_cache()
    cache_url = _cache_key(url, params=params, data=data, json_body=json_body)
    if use_cache and data is None and json_body is None:
        cached = cache.get(cache_url)
        if cached is not None:
            return cached, url

    final_url = url
    try:
        await respect_rate_limit(url, provider.request_delay, provider.request_jitter)
        async with provider.make_session(timeout) as session:
            kwargs = provider.request_kwargs()
            if headers:
                kwargs["headers"] = headers
            if params:
                kwargs["params"] = params
            if json_body is not None:
                kwargs["json"] = json_body
                method = session.post
            elif data is not None:
                kwargs["data"] = data
                method = session.post
            else:
                method = session.get
            async with method(url, allow_redirects=True, **kwargs) as resp:
                final_url = str(resp.url)
                if resp.status != 200:
                    logger.info("[%s] HTTP %s %s", provider.name, resp.status, final_url)
                    return None, final_url
                text = await resp.text(errors="replace")
    except Exception as exc:
        logger.warning("[%s] 请求失败 %s: %s", getattr(provider, "name", "?"), url, exc)
        return None, url

    if use_cache and text:
        cache.set(final_url if data is None and json_body is None and not params else cache_url, text)
    return text, final_url


async def fetch_json(
    provider,
    url: str,
    *,
    params: Optional[dict] = None,
    json_body: Optional[dict] = None,
    headers: Optional[dict] = None,
    use_cache: bool = True,
    timeout: int = 30,
) -> tuple[Optional[Any], str]:
    """返回 (JSON, 最终 URL)。解析失败时 JSON 为 None。"""
    text, final = await fetch_text(
        provider,
        url,
        params=params,
        json_body=json_body,
        headers=headers,
        use_cache=use_cache,
        timeout=timeout,
    )
    if not text:
        return None, final
    try:
        return json.loads(text), final
    except json.JSONDecodeError as exc:
        logger.warning("[%s] JSON 解析失败 %s: %s", getattr(provider, "name", "?"), final, exc)
        return None, final
