"""设置页代理连通性测试（后台线程）。"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from PySide6.QtCore import QThread, Signal

from core.proxy import test_proxy


class ProxyTestWorker(QThread):
    result_signal = Signal(dict)

    def __init__(
        self,
        proxy_cfg: dict[str, Any],
        test_url: Optional[str] = None,
        timeout: float = 5.0,
    ) -> None:
        super().__init__()
        self._proxy_cfg = dict(proxy_cfg)
        if test_url:
            self._proxy_cfg["test_url"] = test_url
        self._timeout = timeout

    def run(self) -> None:
        result = asyncio.run(test_proxy(self._proxy_cfg, timeout=self._timeout))
        self.result_signal.emit(result)
