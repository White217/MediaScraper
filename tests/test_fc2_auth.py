"""FC2 登录会话的离线测试。不打开浏览器，不访问网站。"""

import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from core.fc2_auth import (
    LOGIN_NOT_READY,
    LoginBrowser,
    _wait_for_devtools,
    cookie_header,
    fc2_cookies_from_cdp,
    has_fc2_login,
    load_session,
    save_session,
    session_is_active,
)


def test_session_roundtrip(tmp_path, monkeypatch):
    target = tmp_path / "fc2_session.json"
    monkeypatch.setattr("core.fc2_auth.session_path", lambda: str(target))
    save_session(
        [
            {"name": "session", "value": "abc", "domain": ".fc2.com", "path": "/"},
            {"name": "other", "value": "nope", "domain": ".example.com", "path": "/"},
        ],
        "TestAgent",
    )
    loaded = load_session()
    assert loaded["user_agent"] == "TestAgent"
    assert cookie_header(loaded["cookies"]) == "session=abc"
    assert "example.com" not in cookie_header(loaded["cookies"])


def test_cdp_error_is_not_treated_as_empty_cookies():
    missing = fc2_cookies_from_cdp({
        "id": 1,
        "error": {"code": -32601, "message": "'Network.getAllCookies' wasn't found"},
    })
    assert missing is None
    found = fc2_cookies_from_cdp({
        "id": 2,
        "result": {"cookies": [
            {"name": "FCSID", "value": "1", "domain": ".id.fc2.com"},
            {"name": "MUID", "value": "2", "domain": ".bing.com"},
        ]},
    })
    assert [item["name"] for item in found] == ["FCSID"]
    assert fc2_cookies_from_cdp({"id": 3, "result": {"cookies": []}}) == []


def test_login_cookie_does_not_require_captcha():
    assert has_fc2_login([
        {"name": "language", "value": "cn", "domain": ".fc2.com"},
        {"name": "FCSID", "value": "session", "domain": ".id.fc2.com"},
    ])
    assert not has_fc2_login([
        {"name": "language", "value": "cn", "domain": ".fc2.com"},
        {"name": "_ga", "value": "1", "domain": ".fc2.com"},
    ])
    assert not has_fc2_login([{"name": "fcu", "value": "", "domain": ".fc2.com"}])


def test_saved_login_skips_reopening_the_browser(tmp_path, monkeypatch):
    target = tmp_path / "fc2_session.json"
    monkeypatch.setattr("core.fc2_auth.session_path", lambda: str(target))
    save_session(
        [{"name": "FCSID", "value": "1", "domain": ".id.fc2.com", "path": "/"}],
        "TestAgent",
    )
    assert session_is_active("743165") is True
    browser = LoginBrowser()
    assert browser.capture("743165") == ""


def test_missing_browser_without_session_keeps_waiting(tmp_path, monkeypatch):
    monkeypatch.setattr("core.fc2_auth.session_path", lambda: str(tmp_path / "missing.json"))
    browser = LoginBrowser()
    assert browser.capture("743165") == LOGIN_NOT_READY


def test_devtools_port_counts_after_launcher_exits():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"Browser":"test"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(0)"])
    proc.wait(timeout=10)
    try:
        assert _wait_for_devtools(port, proc, timeout=3) is True
    finally:
        server.shutdown()
