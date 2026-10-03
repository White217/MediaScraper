"""FC2 登录会话。

使用本机 Edge / Chrome 完成登录，只保存 Cookie，不保存密码。
页面若出现验证码，由用户在浏览器里填写；没有验证码时，检测到登录 Cookie 即继续。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import subprocess
import threading
import time
from typing import Optional

import aiohttp

from core.app_paths import data_path
from core.models import ParsedFilename
from core.proxy import get_proxy
from core.scanner import collect_videos
from providers.fc2 import article_url, fc2_article_id, parse_fc2

logger = logging.getLogger(__name__)

# 这些名字只在 FC2 ID 登录成功后出现。语言、统计类 Cookie 不算已登录。
_LOGIN_COOKIE_NAMES = {"fcsid", "fcu", "fcus"}
# capture() 在浏览器还没登录时返回这个值，调用方应继续等待，而不是当成失败。
LOGIN_NOT_READY = "not-ready"


def session_path() -> str:
    return data_path("fc2_session.json")


def browser_profile_dir() -> str:
    path = data_path("fc2_browser")
    os.makedirs(path, exist_ok=True)
    return path


def load_session() -> Optional[dict]:
    path = session_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    cookies = data.get("cookies") if isinstance(data, dict) else None
    if not cookies:
        return None
    return data


def save_session(cookies: list[dict], user_agent: str) -> None:
    path = session_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "user_agent": user_agent,
        "cookies": [
            {
                "name": item.get("name", ""),
                "value": item.get("value", ""),
                "domain": item.get("domain", ""),
                "path": item.get("path", "/"),
            }
            for item in cookies
            if item.get("name") and _is_fc2_domain(item.get("domain", ""))
        ],
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def auth_headers(session: Optional[dict]) -> Optional[dict]:
    if not session:
        return None
    header = cookie_header(session.get("cookies") or [])
    if not header:
        return None
    headers = {"Cookie": header}
    user_agent = (session.get("user_agent") or "").strip()
    if user_agent:
        headers["User-Agent"] = user_agent
    return headers


def has_fc2_login(cookies: list[dict]) -> bool:
    """FC2 ID 已登录。不要求页面出现验证码。"""
    for item in cookies:
        name = (item.get("name") or "").lower()
        if name in _LOGIN_COOKIE_NAMES and item.get("value"):
            return True
    return False


def cookie_header(cookies: list[dict]) -> str:
    parts = []
    for item in cookies:
        name = item.get("name")
        value = item.get("value")
        if name and value is not None:
            parts.append(f"{name}={value}")
    return "; ".join(parts)


def find_fc2_article_id(directory, config: Optional[dict] = None) -> str:
    """在选中的目录或文件里找第一个 FC2 番号，没有则返回空字符串。不改动文件。"""
    scan_cfg = (config or {}).get("scan", {})
    extensions = set(scan_cfg.get("video_extensions") or [])
    exclude = set(scan_cfg.get("exclude_dirs") or [])
    sources = [directory] if isinstance(directory, str) else list(directory)
    try:
        files = collect_videos(
            sources,
            video_extensions=extensions or None,
            scan_subdirs=scan_cfg.get("scan_subdirs", True),
            exclude_dirs=exclude,
            min_file_size_mb=float(scan_cfg.get("min_file_size_mb", 0) or 0),
        )
    except OSError:
        return ""
    for path in files:
        parsed = ParsedFilename(
            original_path=path,
            original_name=os.path.basename(path),
        )
        article_id = fc2_article_id(parsed)
        if article_id:
            return article_id
    return ""


def article_is_public(article_id: str, config: Optional[dict] = None) -> bool:
    """未登录也能打开作品页时，不必弹出浏览器。"""
    try:
        return asyncio.run(_probe(
            article_id, {"cookies": [], "user_agent": ""}, get_proxy(config or {}),
        ))
    except Exception as exc:
        logger.info("FC2 公开页面检查失败: %s", exc)
        return False


def session_is_active(article_id: str, config: Optional[dict] = None) -> bool:
    """已经保存过登录 Cookie 时视为已登录，不再强迫重新打开浏览器。"""
    del article_id, config
    session = load_session()
    if not session:
        return False
    return has_fc2_login(session.get("cookies") or [])


def find_browser() -> Optional[str]:
    local = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("PROGRAMFILES", "")
    program_files_x86 = os.environ.get("PROGRAMFILES(X86)", "")
    candidates = [
        os.path.join(program_files_x86, r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(program_files, r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(program_files, r"Google\Chrome\Application\chrome.exe"),
        os.path.join(program_files_x86, r"Google\Chrome\Application\chrome.exe"),
        os.path.join(local, r"Google\Chrome\Application\chrome.exe"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


class LoginBrowser:
    """用独立配置启动本机浏览器，避免读到用户日常浏览器里的其他网站登录。"""

    def __init__(self) -> None:
        self.proc: Optional[subprocess.Popen] = None
        self.port = 0
        self._capture_lock = threading.Lock()

    def open(self, article_id: str, proxy_url: Optional[str] = None) -> str:
        browser = find_browser()
        if not browser:
            return "未找到本机 Edge 或 Chrome，无法打开 FC2 登录页。"
        self.close()
        self.port = _free_port()
        args = [
            browser,
            f"--user-data-dir={browser_profile_dir()}",
            f"--remote-debugging-port={self.port}",
            "--remote-debugging-address=127.0.0.1",
            "--remote-allow-origins=*",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        if proxy_url:
            args.append(f"--proxy-server={proxy_url}")
        args.append(article_url(article_id))
        try:
            self.proc = subprocess.Popen(args)
        except OSError as exc:
            return f"无法启动浏览器: {exc}"
        if not _wait_for_devtools(self.port, self.proc):
            self.close()
            return "浏览器已启动但调试端口没有就绪。若上次的登录窗口还开着，请先关掉它。"
        return ""

    def capture(self, article_id: str, config: Optional[dict] = None) -> str:
        """读取 Cookie。登录完成返回空字符串，尚未登录返回 LOGIN_NOT_READY。

        不再用作品页是否仍是登录页来拦截。FC2 ID 经常不出现验证码，
        付费提示页也会停在 login.php，那不代表登录没完成。
        """
        del article_id, config
        with self._capture_lock:
            return self._capture_locked()

    def _capture_locked(self) -> str:
        if not self.port:
            # 启动进程可能已经退出，但上次登录的 Cookie 还在。这不是失败。
            if session_is_active("", None):
                return ""
            return LOGIN_NOT_READY
        try:
            cookies, user_agent = asyncio.run(_read_cdp(self.port))
        except Exception as exc:
            logger.info("读取 FC2 浏览器 Cookie 失败: %s", exc)
            return "还没有读到登录会话。请确认登录浏览器仍开着。"
        if not has_fc2_login(cookies):
            return LOGIN_NOT_READY
        save_session(cookies, user_agent)
        return ""

    def close(self) -> None:
        proc = self.proc
        self.proc = None
        self.port = 0
        if proc is None or proc.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            proc.terminate()


def _is_fc2_domain(domain: str) -> bool:
    host = (domain or "").lstrip(".").lower()
    return host == "fc2.com" or host.endswith(".fc2.com")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_devtools(port: int, proc: subprocess.Popen, timeout: float = 15.0) -> bool:
    """等到调试端口可连接。

    Edge 的启动进程经常立刻退出，真正的窗口由另一个进程打开。
    不能因为启动进程结束就判定浏览器没打开。
    """
    del proc
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/json/version"
    while time.time() < deadline:
        try:
            import urllib.request
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


def fc2_cookies_from_cdp(payload: dict) -> Optional[list[dict]]:
    """从一条 CDP 响应里取出 fc2.com Cookie。

    方法不存在或调用失败时返回 None，调用方应改用其他接口。
    成功但没有任何 FC2 Cookie 时返回空列表。
    """
    if payload.get("error") or "result" not in payload:
        return None
    cookies = (payload.get("result") or {}).get("cookies")
    if not isinstance(cookies, list):
        return None
    return [
        item for item in cookies
        if _is_fc2_domain(item.get("domain", ""))
    ]


async def _cdp_call(ws, msg_id: int, method: str) -> dict:
    await ws.send_json({"id": msg_id, "method": method})
    while True:
        message = await ws.receive(timeout=15)
        if message.type != aiohttp.WSMsgType.TEXT:
            raise RuntimeError(f"CDP 连接已关闭: {method}")
        payload = json.loads(message.data)
        if payload.get("id") == msg_id:
            return payload


async def _read_cdp(port: int) -> tuple[list[dict], str]:
    """读取登录浏览器里的 FC2 Cookie。

    Edge 154 的浏览器级调试接口没有 Network.getAllCookies，直接调用会得到
    “method wasn't found”。旧代码把这次失败当成空 Cookie。这里改用
    Storage.getCookies，并在旧版浏览器上退回 Network.getAllCookies。
    """
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(f"http://127.0.0.1:{port}/json/version") as resp:
            info = await resp.json()
        async with session.ws_connect(
            info["webSocketDebuggerUrl"], max_msg_size=8 * 1024 * 1024,
        ) as ws:
            user_agent = ""
            version = await _cdp_call(ws, 1, "Browser.getVersion")
            user_agent = ((version.get("result") or {}).get("userAgent") or "")
            cookies = None
            for msg_id, method in ((2, "Storage.getCookies"), (3, "Network.getAllCookies")):
                cookies = fc2_cookies_from_cdp(await _cdp_call(ws, msg_id, method))
                if cookies is not None:
                    break
        if cookies is None:
            async with session.get(f"http://127.0.0.1:{port}/json") as resp:
                targets = await resp.json()
            page = next(
                (item for item in targets if item.get("type") == "page" and item.get("webSocketDebuggerUrl")),
                None,
            )
            if page is None:
                raise RuntimeError("登录浏览器没有可读取的页面")
            async with session.ws_connect(
                page["webSocketDebuggerUrl"], max_msg_size=8 * 1024 * 1024,
            ) as ws:
                await _cdp_call(ws, 1, "Network.enable")
                cookies = fc2_cookies_from_cdp(await _cdp_call(ws, 2, "Network.getAllCookies"))
        if cookies is None:
            raise RuntimeError("当前浏览器不支持读取 Cookie")
    return cookies, user_agent


async def _probe(article_id: str, session_data: dict, proxy_url: Optional[str]) -> bool:
    headers = auth_headers(session_data) or {}
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        kwargs = {"allow_redirects": True}
        if proxy_url:
            kwargs["proxy"] = proxy_url
        async with session.get(article_url(article_id), **kwargs) as resp:
            if resp.status != 200:
                return False
            text = await resp.text(errors="replace")
            final_url = str(resp.url)
    metadata = parse_fc2(text, article_id, final_url)
    return metadata is not None and bool(metadata.title)
