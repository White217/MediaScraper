"""
GUI 启动入口（供 PyInstaller windowed 打包使用）。
双击 EXE 直接进入图形界面，不弹出控制台窗口。

打包命令见 build_exe.spec / README。
"""

import os
import sys


def _bootstrap_path() -> None:
    """确保 frozen / 开发环境下都能 import 项目模块。"""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    if base not in sys.path:
        sys.path.insert(0, base)


_bootstrap_path()

# Windows 任务栏需要显式 AppUserModelID，才会显示 EXE 自定义图标
# 而不是归到 python.exe 的默认图标。
if os.name == "nt":
    try:
        import ctypes

        from version import __version__

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            f"mediascraper.desktop.{__version__}"
        )
    except Exception:
        pass

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from core.app_paths import resource_path
from core.config import load_config
from core.logging_setup import setup_logging
from gui.main_window import MainWindow

import logging

bootstrap_logger = logging.getLogger("app_gui")


def main() -> int:
    # 打包后无控制台，先初始化文件日志，便于排查启动问题
    config_path = None
    config = load_config(config_path)
    setup_logging(
        config.get("logging", {}).get("level", "INFO"),
        config.get("logging", {}).get("log_to_file", True),
    )
    bootstrap_logger.info("MediaScraper GUI 启动")

    app = QApplication(sys.argv)
    app.setApplicationName("MediaScraper")

    # 全局应用图标（窗口标题栏 / Alt+Tab / 任务栏共用）
    icon_path = resource_path(os.path.join("assets", "app.ico"))
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    window = MainWindow(config_path)
    if os.path.exists(icon_path):
        window.setWindowIcon(QIcon(icon_path))
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
