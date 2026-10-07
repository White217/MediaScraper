"""文件名超出 NAS / Windows 长度时，让用户确认或改写。"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from rename.renamer import prepare_custom_stem


class LongNameDialog(QDialog):
    """列出被自动截断的文件。确定使用输入框里的名称，取消则不写入。"""

    def __init__(self, plans: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("文件名过长")
        self.resize(760, 520)
        self._choices: dict[str, str] = {}
        self._rows: list[_NameRow] = []

        intro = QLabel(
            "文件名正常的文件已经处理完。下面这些标题超过了 NAS 或 Windows 能保存的长度。\n"
            "可以直接修改。确定后保存这些文件；取消则只跳过它们，已经完成的文件不受影响。"
        )
        intro.setWordWrap(True)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(10)
        for plan in plans:
            row = _NameRow(plan)
            self._rows.append(row)
            body_layout.addWidget(row)
        body_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        self._error = QLabel("")
        self._error.setWordWrap(True)
        self._error.setStyleSheet("color: #C0392B;")

        reset_btn = QPushButton("恢复建议名")
        reset_btn.clicked.connect(self._reset)
        ok_btn = QPushButton("确定")
        ok_btn.clicked.connect(self._accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addWidget(reset_btn)
        buttons.addStretch(1)
        buttons.addWidget(ok_btn)
        buttons.addWidget(cancel_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(scroll, 1)
        layout.addWidget(self._error)
        layout.addLayout(buttons)

    def choices(self) -> dict[str, str]:
        return dict(self._choices)

    def _reset(self) -> None:
        self._error.setText("")
        for row in self._rows:
            row.reset()

    def _accept(self) -> None:
        choices: dict[str, str] = {}
        errors: list[str] = []
        for row in self._rows:
            stem, error = prepare_custom_stem(
                row.edit.text(),
                row.plan.name_char_cap,
                row.plan.name_byte_cap,
                row.extension,
            )
            if error:
                errors.append(f"{row.heading()}：{error}")
                row.set_error(True)
            else:
                row.edit.setText(stem)
                row.set_error(False)
                choices[row.plan.source_path] = stem
        if errors:
            self._error.setText("\n".join(errors))
            return
        self._choices = choices
        self._error.setText("")
        self.accept()


class _NameRow(QFrame):
    def __init__(self, plan):
        super().__init__()
        self.plan = plan
        self.extension = os.path.splitext(plan.target_path)[1]
        self.suggested = os.path.splitext(os.path.basename(plan.target_path))[0]
        self.setFrameShape(QFrame.Shape.StyledPanel)

        full = QLabel(plan.full_stem or self.suggested)
        full.setWordWrap(True)
        full.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.edit = QLineEdit(self.suggested)
        self.edit.setClearButtonEnabled(True)
        self.edit.textChanged.connect(self._refresh_meter)

        self.meter = QLabel("")
        self._refresh_meter()

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(self.heading()))
        origin = QLabel(f"原文件：{os.path.basename(plan.source_path)}")
        origin.setWordWrap(True)
        layout.addWidget(origin)
        layout.addWidget(QLabel("完整名称"))
        layout.addWidget(full)
        layout.addWidget(QLabel(f"使用的名称（不要写扩展名 {self.extension}）"))
        layout.addWidget(self.edit)
        layout.addWidget(self.meter)

    def heading(self) -> str:
        code = ""
        if self.plan.metadata and getattr(self.plan.metadata, "code", ""):
            code = self.plan.metadata.code
        elif getattr(self.plan, "part_group", ""):
            code = self.plan.part_group
        return code or os.path.basename(self.plan.source_path)

    def reset(self) -> None:
        self.edit.setText(self.suggested)
        self.set_error(False)

    def set_error(self, failed: bool) -> None:
        self.edit.setStyleSheet("border: 1px solid #C0392B;" if failed else "")

    def _refresh_meter(self) -> None:
        text = self.edit.text()
        used = len(text.encode("utf-8"))
        chars = len(text)
        cap = self.plan.name_byte_cap or 0
        char_cap = self.plan.name_char_cap or 0
        over = (cap and used > cap) or (char_cap and chars > char_cap)
        color = "#C0392B" if over else "#2E7D32"
        self.meter.setStyleSheet(f"color: {color};")
        self.meter.setText(
            f"当前 {used}/{cap} 字节，{chars}/{char_cap} 字。"
            "中文和日文一个字大约占 3 字节。"
        )
