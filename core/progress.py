"""
进度追踪模块
Track pipeline progress with phase-based percentage calculation.

阶段权重按真实耗时分配:
    scanning   5%
    parsing    5%
    searching 25%
    metadata  10%
    image     25%
    apply     30%
"""

import logging
from dataclasses import dataclass
from typing import Callable, Optional

from core.models import ProgressInfo

logger = logging.getLogger(__name__)

# 各阶段权重（百分比，总和 100）。顺序即流水线顺序。
PHASE_WEIGHTS: dict[str, float] = {
    "scanning":  5.0,
    "parsing":   5.0,
    "searching": 25.0,
    "metadata":  10.0,
    "image":     25.0,
    "apply":     30.0,
}

# 阶段别名归一化（orchestrator 使用复数等写法）
PHASE_ALIASES: dict[str, str] = {
    "images": "image",
}

# 阶段在流水线中的顺序
PHASE_ORDER: list[str] = list(PHASE_WEIGHTS.keys())


@dataclass
class ProgressTracker:
    """流水线进度追踪器。

    使用方法:
        tracker = ProgressTracker(total_files=100)
        tracker.start_phase("scanning")
        # ... 处理每个文件时调用 update()
        tracker.update(current=5, message="正在扫描...")
    """

    total_files: int = 0
    _current_phase: str = ""
    _phase_base: float = 0.0          # 当前阶段之前的累积权重
    _phase_weight: float = 0.0        # 当前阶段权重
    _callback: Optional[Callable[[ProgressInfo], None]] = None

    def set_callback(self, callback: Callable[[ProgressInfo], None]) -> None:
        """注册进度回调函数。"""
        self._callback = callback

    def start_phase(self, phase: str) -> None:
        """开始新阶段。"""
        phase = PHASE_ALIASES.get(phase, phase)
        if phase not in PHASE_WEIGHTS:
            logger.warning("未知阶段: %s", phase)
            return

        self._current_phase = phase
        self._phase_weight = PHASE_WEIGHTS[phase]

        # 计算当前阶段之前的累积权重
        idx = PHASE_ORDER.index(phase)
        self._phase_base = sum(PHASE_WEIGHTS[p] for p in PHASE_ORDER[:idx])

        logger.debug("进入阶段: %s (base=%.1f%%, weight=%.1f%%)",
                      phase, self._phase_base, self._phase_weight)

    def update(
        self,
        current: int = 0,
        message: str = "",
        file_name: str = "",
        ratio: Optional[float] = None,
    ) -> ProgressInfo:
        """更新当前阶段进度。

        Args:
            current:   当前已处理文件数。
            message:   状态描述。
            file_name: 当前处理的文件名。
            ratio:     当前阶段完成比例 0~1。提供时优先于 current/total。

        Returns:
            当前进度信息。
        """
        if ratio is not None:
            phase_progress = min(max(float(ratio), 0.0), 1.0)
            percent = self._phase_base + self._phase_weight * phase_progress
        elif self.total_files > 0 and self._phase_weight > 0:
            phase_progress = current / self.total_files
            percent = self._phase_base + self._phase_weight * phase_progress
        else:
            percent = self._phase_base

        info = ProgressInfo(
            phase=self._current_phase,
            current=current,
            total=self.total_files,
            percent=round(min(percent, 100.0), 1),
            message=message,
            file_name=file_name,
        )

        if self._callback:
            self._callback(info)

        return info

    def finish(self, message: str = "完成") -> ProgressInfo:
        """把总进度收到 100%，用于预览等不会进入后续阶段的流程。"""
        info = ProgressInfo(
            phase=self._current_phase or "done",
            current=self.total_files,
            total=self.total_files,
            percent=100.0,
            message=message,
            file_name="",
        )
        if self._callback:
            self._callback(info)
        return info

    @property
    def current_phase(self) -> str:
        return self._current_phase


def format_progress(info: ProgressInfo) -> str:
    """格式化进度信息为可读字符串（CLI 用）。"""
    bar_len = 30
    filled = int(bar_len * info.percent / 100)
    bar = "#" * filled + "-" * (bar_len - filled)
    return f"[{bar}] {info.percent:5.1f}% | {info.phase:<10} | {info.file_name}"
