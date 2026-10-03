"""一次框选文件和文件夹。字幕等非视频也可以被选中，扫描时会跳过。"""

from __future__ import annotations

import os

from PySide6.QtCore import QDir, QSize, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileSystemModel,
    QHBoxLayout,
    QLabel,
    QListView,
    QPushButton,
    QVBoxLayout,
)


class SelectionDialog(QDialog):
    """当前目录的列表，支持框选、Ctrl 和 Shift。文件夹与文件可以同时选中。"""

    def __init__(self, start_dir: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择视频或文件夹")
        self.resize(720, 480)
        self._current = start_dir if os.path.isdir(start_dir) else os.path.dirname(start_dir)
        if not self._current or not os.path.isdir(self._current):
            self._current = os.path.expanduser("~")

        self._model = QFileSystemModel(self)
        self._model.setFilter(
            QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot | QDir.Filter.AllDirs
        )
        self._model.setRootPath(self._current)
        self._model.directoryLoaded.connect(self._on_loaded)

        self._view = QListView()
        self._view.setModel(self._model)
        self._view.setViewMode(QListView.ViewMode.ListMode)
        self._view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._view.setSelectionRectVisible(True)
        self._view.setUniformItemSizes(True)
        self._view.setWrapping(False)
        self._view.setSpacing(1)
        self._view.setIconSize(QSize(16, 16))
        self._view.doubleClicked.connect(self._enter)

        self._path_label = QLabel(self._current)
        self._path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._path_label.setWordWrap(True)

        up_btn = QPushButton("上一级")
        up_btn.clicked.connect(self._up)
        all_btn = QPushButton("全选")
        all_btn.clicked.connect(self._view.selectAll)
        ok_btn = QPushButton("确定")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addWidget(up_btn)
        buttons.addWidget(all_btn)
        buttons.addStretch(1)
        buttons.addWidget(ok_btn)
        buttons.addWidget(cancel_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(self._path_label)
        layout.addWidget(self._view, 1)
        layout.addLayout(buttons)
        self._show_dir(self._current)

    def _on_loaded(self, path: str) -> None:
        if os.path.normcase(os.path.abspath(path)) == os.path.normcase(self._current):
            self._view.setRootIndex(self._model.index(self._current))

    def _show_dir(self, directory: str) -> None:
        directory = os.path.abspath(directory)
        self._current = directory
        self._path_label.setText(directory)
        self._model.setRootPath(directory)
        self._view.setRootIndex(self._model.index(directory))

    def _enter(self, index) -> None:
        path = self._model.filePath(index)
        if os.path.isdir(path):
            self._show_dir(path)

    def _up(self) -> None:
        parent = os.path.dirname(self._current)
        if parent and parent != self._current:
            self._show_dir(parent)

    def selected_paths(self) -> list[str]:
        paths: list[str] = []
        seen: set[str] = set()
        for index in self._view.selectionModel().selectedIndexes():
            if index.column() != 0:
                continue
            path = os.path.normpath(self._model.filePath(index))
            key = os.path.normcase(path)
            if not path or key in seen:
                continue
            seen.add(key)
            paths.append(path)
        return paths
