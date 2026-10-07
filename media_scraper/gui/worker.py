"""
Background worker for scraping pipeline.
Runs the scraper in a separate thread to keep GUI responsive.
Supports cooperative cancellation via a threading.Event bridged into asyncio.
"""

import asyncio
import logging
import os
import queue
import threading
from typing import Optional

from PySide6.QtCore import QThread, Signal

from core.config import load_config
from core.orchestrator import Pipeline

logger = logging.getLogger(__name__)


class ScrapeWorker(QThread):
    """Background QThread running the media pipeline in its own event loop."""

    progress_update = Signal(dict)      # 总进度 info dict
    finished_signal = Signal(list)      # List of rename plans
    error_signal = Signal(str)          # Error message
    log_signal = Signal(str)            # Log message
    cancelled_signal = Signal(str)      # Cancellation notice
    file_list_signal = Signal(list)     # 扫描+解析完成：文件列表（用于立即建行）
    file_progress_signal = Signal(dict)  # 单文件：{index,status,stage}
    long_name_signal = Signal(list)     # 超长文件名，等待主线程确认

    def __init__(self, directory, config_path: Optional[str] = None,
                 dry_run: bool = True, parent=None):
        super().__init__(parent)
        self.directory = directory
        self.config_path = config_path
        self.dry_run = dry_run
        # 线程安全的取消标志：GUI 线程 set，worker/asyncio 侧轮询
        self._cancel_event = threading.Event()
        self._long_name_queue: queue.Queue = queue.Queue()
        # 进度限频：距上次发射不足 100ms 的中间进度丢弃，收尾进度强制发
        self._last_progress_ts = 0.0
        import time as _time
        self._time = _time

    def cancel(self):
        """Thread-safe cancellation request."""
        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def provide_long_names(self, choices) -> None:
        """主线程把用户确认的名称交回工作线程。None 表示取消写入。"""
        self._long_name_queue.put(choices)

    def ask_long_names(self, plans: list):
        """在工作线程里阻塞，直到主线程关闭确认窗口。"""
        self.long_name_signal.emit(list(plans))
        return self._long_name_queue.get()

    def run(self):
        """Run the pipeline inside a fresh asyncio event loop on this thread."""
        try:
            config = load_config(self.config_path)
            pipeline = Pipeline(config)
            # 把取消事件交给 pipeline，使其内部循环可协作式退出
            pipeline.set_cancel_event(self._cancel_event)

            def on_progress(info):
                if self._cancel_event.is_set():
                    return
                payload = {
                    "phase": info.phase,
                    "current": info.current,
                    "total": info.total,
                    "percent": info.percent,
                    "message": info.message,
                    "file_name": info.file_name,
                }
                now = self._time.monotonic()
                # 收尾（当前==总数）或距上次≥100ms 才发射，合并中间刷新
                is_final = info.total and info.current >= info.total
                if is_final or now - self._last_progress_ts >= 0.1:
                    self._last_progress_ts = now
                    self.progress_update.emit(payload)

            pipeline.set_progress_callback(on_progress)

            # 文件级事件：列表 / 单文件阶段进度，直接转发给主线程
            def on_file_event(event):
                if self._cancel_event.is_set():
                    return
                etype = event.get("type")
                if etype == "file_list":
                    self.file_list_signal.emit(event.get("files", []))
                elif etype == "file":
                    self.file_progress_signal.emit({
                        "index": event.get("index"),
                        "status": event.get("status"),
                        "stage": event.get("stage", 0),
                    })

            pipeline.set_file_event_callback(on_file_event)
            pipeline.set_long_name_callback(self.ask_long_names)

            sources = self.directory if isinstance(self.directory, (list, tuple)) else [self.directory]
            anchor = sources[0]
            csv_dir = anchor if os.path.isdir(anchor) else os.path.dirname(anchor)
            csv_path = os.path.join(csv_dir, "scrape_plan.csv")

            from core.http_sessions import close_all_sessions, reset_async_resources

            reset_async_resources()
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                if self.dry_run:
                    plans = loop.run_until_complete(
                        pipeline.run_full_async(self.directory, output_csv=csv_path)
                    )
                else:
                    plans = loop.run_until_complete(
                        pipeline.run_apply_async(self.directory, output_csv=csv_path)
                    )
            finally:
                try:
                    loop.run_until_complete(close_all_sessions())
                except Exception:
                    logger.debug("关闭 HTTP 会话时出错", exc_info=True)
                try:
                    pending = asyncio.all_tasks(loop)
                    for t in pending:
                        t.cancel()
                    if pending:
                        loop.run_until_complete(
                            asyncio.gather(*pending, return_exceptions=True)
                        )
                finally:
                    loop.close()
                    reset_async_resources()

            if self._cancel_event.is_set():
                self.cancelled_signal.emit("操作已中断，已停止后续任务。")
                return

            self.log_signal.emit(f"流水线结束，共 {len(plans)} 条计划。")
            self.finished_signal.emit(plans)

        except asyncio.CancelledError:
            self.cancelled_signal.emit("操作已中断。")
        except Exception as e:  # noqa: BLE001 - 兜底，绝不让线程崩溃
            logger.exception("Worker error")
            self.error_signal.emit(str(e))
