"""
进度追踪模块的单元测试
Unit tests for core.progress module.
"""

import pytest

from core.progress import ProgressTracker, format_progress, PHASE_WEIGHTS
from core.models import ProgressInfo


class TestProgressTracker:
    """进度追踪器测试。"""

    def test_start_phase(self):
        tracker = ProgressTracker(total_files=10)
        tracker.start_phase("scanning")
        assert tracker.current_phase == "scanning"

    def test_progress_calculation(self):
        tracker = ProgressTracker(total_files=10)
        tracker.start_phase("scanning")
        info = tracker.update(current=5, message="halfway")
        # scanning 权重 5%，50% 完成 → 2.5%
        assert info.percent == 2.5

    def test_second_phase(self):
        tracker = ProgressTracker(total_files=10)
        tracker.start_phase("scanning")
        tracker.update(current=10)
        tracker.start_phase("parsing")
        info = tracker.update(current=5)
        # scanning 5% + parsing 权重 5% 的一半 = 7.5%
        assert info.percent == 7.5

    def test_callback_invoked(self):
        tracker = ProgressTracker(total_files=10)
        callback_data = []
        tracker.set_callback(lambda info: callback_data.append(info))
        tracker.start_phase("scanning")
        tracker.update(current=3, message="test", file_name="test.mp4")
        assert len(callback_data) == 1
        assert callback_data[0].current == 3

    def test_format_progress(self):
        info = ProgressInfo(
            phase="scanning",
            current=5,
            total=10,
            percent=50.0,
            message="test",
            file_name="movie.mkv",
        )
        output = format_progress(info)
        assert "50.0%" in output
        assert "scanning" in output
        assert "movie.mkv" in output
        assert "#" in output  # ASCII progress bar

    def test_phase_weights_sum_to_100(self):
        total = sum(PHASE_WEIGHTS.values())
        assert total == 100.0

    def test_apply_phase_starts_after_downloads(self):
        tracker = ProgressTracker(total_files=10)
        tracker.start_phase("apply")
        info = tracker.update(current=0, ratio=0)
        # 扫描5 + 解析5 + 搜索25 + 元数据10 + 封面25 = 70
        assert info.percent == 70.0
