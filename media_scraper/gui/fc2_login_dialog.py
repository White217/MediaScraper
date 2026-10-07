"""刮削前的 FC2 登录。已有有效会话时只提示，否则打开本机浏览器。"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from core.fc2_auth import LOGIN_NOT_READY, LoginBrowser, article_is_public, session_is_active
from core.proxy import get_proxy


def ensure_fc2_login(parent, article_id: str, config: Optional[dict] = None) -> bool:
    """返回 True 表示可以开始刮削。用户取消登录时返回 False。"""
    if session_is_active(article_id, config):
        QMessageBox.information(parent, "FC2", "FC2登录已完成")
        return True
    if article_is_public(article_id, config):
        return True
    dialog = Fc2LoginDialog(article_id, config, parent)
    return dialog.exec() == QDialog.DialogCode.Accepted


class _LoginWatch(QThread):
    """后台查看登录 Cookie，避免在主界面里空等验证码。"""

    succeeded = Signal()
    failed = Signal(str)

    def __init__(self, browser: LoginBrowser, article_id: str, config: dict):
        super().__init__()
        self._browser = browser
        self._article_id = article_id
        self._config = config
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        while not self._stop:
            error = self._browser.capture(self._article_id, self._config)
            if self._stop:
                return
            if not error:
                self.succeeded.emit()
                return
            if error != LOGIN_NOT_READY:
                self.failed.emit(error)
                return
            self.msleep(1200)


class Fc2LoginDialog(QDialog):
    def __init__(self, article_id: str, config: Optional[dict], parent=None):
        super().__init__(parent)
        self._article_id = article_id
        self._config = config or {}
        self._browser = LoginBrowser()
        self._watcher: Optional[_LoginWatch] = None
        self.setWindowTitle("FC2 登录")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "这次任务包含 FC2 番号。请在打开的浏览器中登录 FC2。\n"
            "如果页面出现验证码，请手动填写。没有验证码时，检测到登录完成会自动继续。"
        ))
        self._status = QLabel("正在打开浏览器…")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        buttons = QHBoxLayout()
        self._open_btn = QPushButton("打开登录页面")
        self._open_btn.clicked.connect(self._open_browser)
        buttons.addWidget(self._open_btn)
        self._done_btn = QPushButton("我已登录完成")
        self._done_btn.clicked.connect(self._confirm)
        buttons.addWidget(self._done_btn)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

    def showEvent(self, event):
        super().showEvent(event)
        if self._browser.proc is None and self._watcher is None:
            self._open_browser()

    def _open_browser(self) -> None:
        self._stop_watch()
        self._status.setText("正在打开浏览器…")
        error = self._browser.open(self._article_id, get_proxy(self._config))
        if error:
            self._status.setText(error)
            return
        self._status.setText("浏览器已打开。请登录 FC2，完成后会自动继续。")
        self._start_watch()

    def _start_watch(self) -> None:
        self._stop_watch()
        watcher = _LoginWatch(self._browser, self._article_id, self._config)
        watcher.succeeded.connect(self._on_ready, Qt.ConnectionType.QueuedConnection)
        watcher.failed.connect(self._on_watch_failed, Qt.ConnectionType.QueuedConnection)
        self._watcher = watcher
        watcher.start()

    def _stop_watch(self) -> None:
        watcher = self._watcher
        self._watcher = None
        if watcher is None:
            return
        watcher.stop()
        watcher.wait(5000)

    def _on_ready(self) -> None:
        self._stop_watch()
        self._browser.close()
        self.accept()

    def _on_watch_failed(self, error: str) -> None:
        self._status.setText(error)

    def _confirm(self) -> None:
        self._done_btn.setEnabled(False)
        self._stop_watch()
        try:
            error = self._browser.capture(self._article_id, self._config)
        finally:
            self._done_btn.setEnabled(True)
        if error == LOGIN_NOT_READY:
            self._status.setText("还没有检测到 FC2 登录。请先在浏览器中完成登录。")
            self._start_watch()
            return
        if error:
            self._status.setText(error)
            return
        self._on_ready()

    def reject(self) -> None:
        self._stop_watch()
        self._browser.close()
        super().reject()
