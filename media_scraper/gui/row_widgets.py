# -*- coding: utf-8 -*-
"""
表格行内控件：单文件“处理进度”进度条 + “状态”单元格工厂。

进度条采用阶段阶梯（0/30/60/80/100%）+ 状态颜色：
    waiting  等待中   灰色   0%
    running  处理中   蓝色   阶梯
    success  成功     绿色   100%
    failed   失败     红色   停住
    skipped  已跳过   灰色   0%
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QTableWidgetItem,
    QWidget,
)

# ---- 状态标识（内部 key） ----
WAITING = "waiting"
RUNNING = "running"
SUCCESS = "success"
FAILED = "failed"
SKIPPED = "skipped"

# ---- 状态 -> 中文显示文本 ----
STATUS_TEXT = {
    WAITING: "等待中",
    RUNNING: "处理中",
    SUCCESS: "成功",
    FAILED: "失败",
    SKIPPED: "已跳过",
}

# ---- 各阶段阶梯进度（百分比） ----
STAGE_START = 30      # 开始搜索
STAGE_DETAIL = 60     # 拿到详情
STAGE_WRITTEN = 80    # 封面 / 元数据写完
STAGE_DONE = 100      # 成功归档

# 自定义数据角色：在状态单元格上记录状态 key
STATUS_ROLE = Qt.UserRole + 1

# 状态前景色（明 / 暗通用，挑选在两套背景下都清晰的颜色）
STATUS_COLORS = {
    WAITING: QColor("#8a94a8"),
    RUNNING: QColor("#4f8dff"),
    SUCCESS: QColor("#2fbf71"),
    FAILED:  QColor("#ff5252"),
    SKIPPED: QColor("#8a94a8"),
}


def color_for_status(status: str) -> QColor:
    return QColor(STATUS_COLORS.get(status, STATUS_COLORS[WAITING]))


class RowProgressBar(QProgressBar):
    """表格单元格内的单文件进度条，通过 status 动态属性切换 QSS 配色。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRange(0, 100)
        self.setValue(0)
        self.setTextVisible(True)          # 显示百分比文字
        self.setFixedHeight(20)
        self._status = WAITING
        self.set_status(WAITING)

    def set_status(self, status: str, value: int = None):
        """更新状态（动态属性），可选同时更新数值，并重绘以应用 QSS。"""
        self._status = status
        self.setProperty("status", status)
        if value is not None:
            self.setValue(int(value))
        # 动态属性切换后必须重新 polish 才能应用新 QSS
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def status(self) -> str:
        return self._status


def make_status_item(status: str = WAITING) -> QTableWidgetItem:
    """创建“状态”列单元格：居中、按状态着色，并用数据角色记录状态。"""
    item = QTableWidgetItem(STATUS_TEXT.get(status, ""))
    item.setTextAlignment(Qt.AlignCenter)
    item.setData(STATUS_ROLE, status)
    item.setForeground(QBrush(color_for_status(status)))
    # 状态文本不允许被编辑
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    return item


def update_status_item(item: QTableWidgetItem, status: str) -> None:
    """更新状态单元格的文本、颜色与记录的状态。"""
    item.setText(STATUS_TEXT.get(status, ""))
    item.setData(STATUS_ROLE, status)
    item.setForeground(QBrush(color_for_status(status)))


class StatusBadge(QWidget):
    """状态列的圆角标签。文本和配色由 QSS 的 status 属性决定。"""

    def __init__(self, status: str = WAITING, parent=None):
        super().__init__(parent)
        self.setObjectName("statusBadgeHost")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 3, 8, 3)
        layout.setSpacing(0)
        self._label = QLabel()
        self._label.setObjectName("statusBadge")
        self._label.setAlignment(Qt.AlignCenter)
        # 列宽不够时保持文字原样，不把「处理中」挤成扁字
        self._label.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        layout.addWidget(self._label, 0, Qt.AlignLeft | Qt.AlignVCenter)
        self.set_status(status)

    def set_status(self, status: str) -> None:
        text = STATUS_TEXT.get(status, "")
        self._label.setText(text)
        text_width = self._label.fontMetrics().horizontalAdvance(text)
        self._label.setMinimumWidth(text_width + 28)
        self._label.setProperty("status", status)
        self._label.style().unpolish(self._label)
        self._label.style().polish(self._label)
        self._label.update()

    def status(self) -> str:
        return self._label.property("status") or WAITING
