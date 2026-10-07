"""统一代理配置与 HTTP 客户端工厂。"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Optional
from urllib.parse import quote

import aiohttp

from core.app_paths import writable_root
from core.http_sessions import DEFAULT_USER_AGENT
from providers.http_client import HTTPClient

logger = logging.getLogger(__name__)

_DEFAULT_TEST_URL = "https://www.gstatic.com/generate_204"


def _safe_message(exc: BaseException) -> str:
    try:
        return str(exc) or exc.__class__.__name__
    except Exception:
        return exc.__class__.__name__


def _parse_proxy_url(url: str) -> dict[str, Any]:
    from urllib.parse import urlparse

    text = url.strip()
    if "://" not in text:
        text = "http://" + text
    parsed = urlparse(text)
    scheme = (parsed.scheme or "http").lower()
    ptype = "socks5" if scheme.startswith("socks") else "http"
    return {
        "type": ptype,
        "host": parsed.hostname or "",
        "port": parsed.port or 0,
        "username": parsed.username or "",
        "password": parsed.password or "",
        "remote_dns": scheme.endswith("h"),
    }


def normalize_proxy_config(raw: Any) -> dict[str, Any]:
    """兼容旧版字符串与新版结构化字典。字符串里的端口优先于默认值。"""
    if isinstance(raw, dict):
        cfg = dict(raw)
    elif isinstance(raw, str) and raw.strip():
        cfg = {"enabled": True, "url": raw.strip()}
    else:
        cfg = {}

    parsed_url: dict[str, Any] = {}
    url = cfg.get("url")
    if isinstance(url, str) and url.strip():
        parsed_url = _parse_proxy_url(url)

    if cfg.get("enabled") is None:
        cfg["enabled"] = bool(parsed_url.get("host") or cfg.get("host"))

    ptype = str(cfg.get("type") or parsed_url.get("type") or "http").lower()
    if ptype == "https":
        ptype = "http"
    cfg["type"] = ptype if ptype in ("http", "socks5") else "http"

    cfg["host"] = cfg.get("host") or parsed_url.get("host") or "127.0.0.1"
    port = cfg.get("port") or parsed_url.get("port") or 7890
    cfg["port"] = int(port)
    cfg["username"] = cfg.get("username") or parsed_url.get("username") or ""
    cfg["password"] = cfg.get("password") or parsed_url.get("password") or ""
    cfg.setdefault("test_url", _DEFAULT_TEST_URL)
    if "remote_dns" not in cfg:
        cfg["remote_dns"] = parsed_url.get("remote_dns", True)
    return cfg


def build_proxy_url(cfg: dict[str, Any]) -> str:
    if not cfg.get("enabled"):
        return ""
    host = str(cfg.get("host", "")).strip()
    if not host:
        return ""
    port = int(cfg.get("port") or 0)
    user = str(cfg.get("username") or "")
    pwd = str(cfg.get("password") or "")
    auth = ""
    if user:
        auth = f"{quote(user, safe='')}:{quote(pwd, safe='')}@"

    if cfg.get("type") == "socks5":
        scheme = "socks5h" if cfg.get("remote_dns", True) else "socks5"
        return f"{scheme}://{auth}{host}:{port}"

    return f"http://{auth}{host}:{port}"


def make_connector(cfg: dict[str, Any]) -> Any:
    if cfg.get("type") != "socks5" or not cfg.get("enabled"):
        return None
    from aiohttp_socks import ProxyConnector

    host = cfg.get("host", "")
    port = int(cfg.get("port") or 0)
    user = cfg.get("username") or None
    pwd = cfg.get("password") or None
    rdns = bool(cfg.get("remote_dns", True))
    return ProxyConnector(
        host=host,
        port=port,
        username=user,
        password=pwd,
        rdns=rdns,
    )


def get_proxy(config: dict[str, Any]) -> str:
    raw = config.get("http", {}).get("proxy", "")
    cfg = normalize_proxy_config(raw)
    return build_proxy_url(cfg)


def get_test_url(config: dict[str, Any]) -> str:
    raw = config.get("http", {}).get("proxy", "")
    cfg = normalize_proxy_config(raw)
    return str(cfg.get("test_url") or _DEFAULT_TEST_URL)


def create_http_client(config: Optional[dict[str, Any]] = None) -> HTTPClient:
    cfg = config or {}
    http = cfg.get("http", {})
    raw_proxy = http.get("proxy", "")
    proxy_cfg = normalize_proxy_config(raw_proxy)
    proxy_url = build_proxy_url(proxy_cfg)
    connector = make_connector(proxy_cfg) if proxy_cfg.get("type") == "socks5" else None
    cache_dir = http.get("cache_dir", "cache")
    if cache_dir and not os.path.isabs(cache_dir):
        cache_dir = os.path.join(writable_root(), cache_dir)
    return HTTPClient(
        request_delay=float(http.get("request_delay", 0.3)),
        timeout=int(http.get("timeout", 30)),
        max_retries=int(http.get("max_retries", 3)),
        user_agent=http.get("user_agent") or DEFAULT_USER_AGENT,
        proxy=proxy_url or None,
        connector=connector,
        cache_dir=cache_dir if http.get("cache_enabled", True) else None,
    )


def classify_error(exc: BaseException) -> str:
    msg = _safe_message(exc)
    low = msg.lower().replace("ssl:default", "")
    if "timeout" in low or isinstance(exc, asyncio.TimeoutError):
        return "连接超时"
    if "拒绝" in msg or "refused" in low or "1225" in low or "10061" in low:
        return "代理拒绝连接"
    if "certificate" in low or "certificate_verify" in low:
        return "SSL 证书错误"
    if "407" in low:
        return "代理认证失败"
    if "name or service not known" in low or "getaddrinfo" in low:
        return "DNS 解析失败"
    return msg


async def test_proxy(raw: Any, timeout: float = 5.0) -> dict[str, Any]:
    cfg = normalize_proxy_config(raw)
    if not cfg.get("enabled"):
        return {"ok": True, "message": "未启用代理", "status": 0}

    url = str(cfg.get("test_url") or _DEFAULT_TEST_URL)
    proxy_url = build_proxy_url(cfg)
    if not proxy_url:
        return {"ok": False, "message": "代理主机未配置", "status": 0}

    connector = make_connector(cfg) if cfg.get("type") == "socks5" else None
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    started = time.perf_counter()
    try:
        async with aiohttp.ClientSession(timeout=client_timeout, connector=connector) as session:
            kwargs: dict[str, Any] = {}
            if cfg.get("type") != "socks5":
                kwargs["proxy"] = proxy_url
            async with session.get(url, **kwargs) as resp:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                if resp.status < 400:
                    return {
                        "ok": True,
                        "message": f"连通 ({resp.status}, {elapsed_ms}ms)",
                        "status": resp.status,
                    }
                return {
                    "ok": False,
                    "message": f"HTTP {resp.status}",
                    "status": resp.status,
                }
    except Exception as exc:
        return {
            "ok": False,
            "message": classify_error(exc),
            "status": 0,
        }
