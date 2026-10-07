"""主题管理 gui/theme.py

负责加载 assets 下的 QSS、在运行时切换明暗主题，并把选择持久化到 config。
所有路径经 resource_path() 解析，兼容 PyInstaller 打包环境。
"""
from __future__ import annotations

import os

from PySide6.QtWidgets import QApplication

from core.app_paths import resource_path

THEMES = {
    "light": "浅色",
    "dark": "暗色",
}
DEFAULT_THEME = "light"

_QSS_CACHE: dict[str, str] = {}


def _qss_path(theme: str) -> str:
    return resource_path(os.path.join("assets", f"{theme}.qss"))


def load_stylesheet(theme: str) -> str:
    """读取指定主题的 QSS 文本（带缓存）。找不到则返回空串。"""
    if theme not in THEMES:
        theme = DEFAULT_THEME
    if theme in _QSS_CACHE:
        return _QSS_CACHE[theme]
    path = _qss_path(theme)
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        text = ""
    _QSS_CACHE[theme] = text
    return text


def apply_theme(theme: str) -> str:
    """把指定主题应用到当前 QApplication，返回实际使用的主题名。"""
    if theme not in THEMES:
        theme = DEFAULT_THEME
    app = QApplication.instance()
    if app is not None:
        # Fusion 让圆角、卡片边框在 Windows 上按 QSS 绘制。
        app.setStyle("Fusion")
        app.setStyleSheet(load_stylesheet(theme))
    return theme


def get_theme_from_config(config: dict | None) -> str:
    """从 config 读取主题字段，缺省/非法值回退默认浅色。"""
    if not config:
        return DEFAULT_THEME
    theme = config.get("ui", {}).get("theme", DEFAULT_THEME)
    return theme if theme in THEMES else DEFAULT_THEME


def save_theme_to_config(config: dict, theme: str) -> dict:
    """把主题写回 config 的 ui 段（就地修改并返回）。"""
    if theme not in THEMES:
        theme = DEFAULT_THEME
    config.setdefault("ui", {})["theme"] = theme
    return config
