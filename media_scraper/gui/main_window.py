"""
Main window for MediaScraper GUI.
"""

import logging
import os
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QFont, QIcon
from PySide6.QtWidgets import (
    QFileDialog,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QCheckBox,
    QGridLayout,
    QScrollArea,
    QHeaderView,
)

from core.config import load_config, save_config
from providers.choices import PROVIDER_CHOICES, apply_provider_selection, ensure_known_providers
from core.app_paths import config_file_path, resource_path
from version import __version__
from gui.fc2_login_dialog import ensure_fc2_login
from gui.settings_dialog import SettingsDialog
from gui.theme import (
    THEMES,
    apply_theme,
    get_theme_from_config,
    save_theme_to_config,
)
from gui.worker import ScrapeWorker
from gui.row_widgets import (
    RowProgressBar,
    StatusBadge,
    make_status_item,
    update_status_item,
    WAITING,
    RUNNING,
    SUCCESS,
    FAILED,
    SKIPPED,
    STAGE_START,
    STAGE_DETAIL,
    STAGE_WRITTEN,
    STAGE_DONE,
    STATUS_ROLE,
)

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self, config_path: Optional[str] = None):
        super().__init__()
        self.config_path = config_path
        self.config = ensure_known_providers(load_config(config_path))
        self.worker: Optional[ScrapeWorker] = None
        self.plans: list = []

        self.setWindowTitle(f"MediaScraper {__version__}")
        self.resize(1280, 820)
        icon_path = resource_path(os.path.join("assets", "app.ico"))
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        # 应用启动时恢复上次主题（默认浅色）
        self.current_theme = get_theme_from_config(self.config)
        apply_theme(self.current_theme)

        self._build_menu()
        self._build_ui()
        self._load_paths_from_config()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("pageRoot")
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_sidebar())
        root.addWidget(self._build_center(), 1)
        root.addWidget(self._build_rail())
        self._sync_source_checks()
        self._show_sheet(0, "work")

    def _nav_button(self, text: str, slot, checkable: bool = True) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("navBtn")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setCheckable(checkable)
        button.clicked.connect(slot)
        return button

    def _build_sidebar(self) -> QFrame:
        side = QFrame()
        side.setObjectName("sideNav")
        side.setFixedWidth(176)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(12, 18, 12, 14)
        layout.setSpacing(4)

        brand = QLabel("MediaScraper")
        brand.setObjectName("brandMark")
        layout.addWidget(brand)
        layout.addSpacing(10)

        self._nav_work = self._nav_button("工作台", lambda: self._show_sheet(0, "work"))
        self._nav_log = self._nav_button("日志", lambda: self._show_sheet(1, "log"))
        self._nav_settings = self._nav_button("设置", self._open_settings, checkable=False)
        for button in (self._nav_work, self._nav_log, self._nav_settings):
            layout.addWidget(button)

        layout.addStretch(1)
        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("overallStatus")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("overallProgress")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)
        return side

    def _build_center(self) -> QWidget:
        host = QWidget()
        host.setObjectName("sheetHost")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(20, 16, 8, 16)
        layout.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(8)
        title = QLabel("媒体刮削工作台")
        title.setObjectName("pageTitle")
        top.addWidget(title)
        top.addStretch(1)
        theme_label = QLabel("主题")
        theme_label.setObjectName("fieldLabel")
        top.addWidget(theme_label)
        self.theme_combo = QComboBox()
        self.theme_combo.setObjectName("themeCombo")
        for key, name in THEMES.items():
            self.theme_combo.addItem(name, key)
        self._sync_theme_combo()
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        top.addWidget(self.theme_combo)
        layout.addLayout(top)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        btn_folders = QPushButton("选择文件夹")
        btn_folders.clicked.connect(self._browse_folders)
        btn_videos = QPushButton("选择视频")
        btn_videos.clicked.connect(self._browse_videos)
        btn_remove = QPushButton("移除")
        btn_remove.clicked.connect(self._remove_inputs)
        self.btn_scan = QPushButton("扫描预览")
        self.btn_scan.setToolTip("只在线查询并生成计划，不写入刮削文件、不移动视频")
        self.btn_scan.clicked.connect(self._on_scan)
        self.btn_apply = QPushButton("执行刮削")
        self.btn_apply.setObjectName("primaryBtn")
        self.btn_apply.setToolTip("刮削并实际重命名/移动文件")
        self.btn_apply.clicked.connect(self._on_apply)
        self.btn_cancel = QPushButton("中断")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._on_cancel)
        actions.addWidget(btn_folders)
        actions.addWidget(btn_videos)
        actions.addWidget(btn_remove)
        actions.addStretch(1)
        actions.addWidget(self.btn_scan)
        actions.addWidget(self.btn_apply)
        actions.addWidget(self.btn_cancel)
        layout.addLayout(actions)

        kpis = QHBoxLayout()
        kpis.setSpacing(12)
        self._kpi_labels = {}
        for key, caption in (
            (WAITING, "待处理"),
            (RUNNING, "刮削中"),
            (SUCCESS, "成功"),
            (FAILED, "失败"),
        ):
            card, value = self._kpi_card(caption)
            self._kpi_labels[key] = value
            kpis.addWidget(card, 1)
        layout.addLayout(kpis)

        self._sheets = QStackedWidget()
        self._sheets.addWidget(self._build_work_page())
        self._sheets.addWidget(self._build_log_page())
        layout.addWidget(self._sheets, 1)
        return host

    def _kpi_card(self, caption: str) -> tuple[QFrame, QLabel]:
        card = QFrame()
        card.setObjectName("kpiCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(2)
        label = QLabel(caption)
        label.setObjectName("kpiLabel")
        value = QLabel("0")
        value.setObjectName("kpiValue")
        layout.addWidget(label)
        layout.addWidget(value)
        return card, value

    def _build_work_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("sheetHost")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(self._build_task_strip())
        layout.addWidget(self._build_table_page(), 1)
        return page

    def _build_task_strip(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)
        head = QHBoxLayout()
        heading = QLabel("本次任务")
        heading.setObjectName("cardTitle")
        self._task_count = QLabel("已选 0 个")
        self._task_count.setObjectName("sourceCount")
        head.addWidget(heading)
        head.addStretch(1)
        head.addWidget(self._task_count)
        layout.addLayout(head)

        self.input_list = QListWidget()
        self.input_list.setObjectName("taskList")
        self.input_list.setFlow(QListWidget.Flow.LeftToRight)
        self.input_list.setWrapping(False)
        self.input_list.setFixedHeight(44)
        self.input_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.input_list.setHorizontalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        layout.addWidget(self.input_list)
        return card

    def _build_table_page(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 10)
        layout.setSpacing(8)
        heading = QLabel("处理列表")
        heading.setObjectName("cardTitle")
        layout.addWidget(heading)

        self.result_table = QTableWidget(0, 9)
        self.result_table.setHorizontalHeaderLabels([
            "序号", "原文件名", "番号", "标题", "年份", "刮削源", "评分",
            "处理进度", "状态",
        ])
        self.result_table.setAlternatingRowColors(False)
        self.result_table.setShowGrid(False)
        self.result_table.verticalHeader().setVisible(False)
        self.result_table.verticalHeader().setDefaultSectionSize(36)
        self.result_table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.result_table.setWordWrap(False)
        self.result_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        header = self.result_table.horizontalHeader()
        header.setMinimumSectionSize(48)
        for column in (0, 4, 6):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(8, QHeaderView.ResizeMode.Fixed)
        self.result_table.setColumnWidth(1, 120)
        self.result_table.setColumnWidth(2, 88)
        self.result_table.setColumnWidth(7, 118)
        self.result_table.setColumnWidth(8, 120)
        header.setStretchLastSection(False)
        layout.addWidget(self.result_table, 1)
        return card

    def _build_log_page(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        heading = QLabel("日志")
        heading.setObjectName("cardTitle")
        layout.addWidget(heading)
        self.log_view = QTextEdit()
        self.log_view.setObjectName("logView")
        self.log_view.setReadOnly(True)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(9)
        self.log_view.setFont(mono)
        layout.addWidget(self.log_view, 1)
        return card

    def _build_rail(self) -> QFrame:
        rail = QFrame()
        rail.setObjectName("rightRail")
        rail.setFixedWidth(300)
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(8, 16, 16, 16)
        layout.setSpacing(12)

        source_card = QFrame()
        source_card.setObjectName("card")
        source_layout = QVBoxLayout(source_card)
        source_layout.setContentsMargins(14, 12, 14, 12)
        source_layout.setSpacing(8)
        head = QHBoxLayout()
        heading = QLabel("本次刮削源")
        heading.setObjectName("cardTitle")
        self._source_count = QLabel("已选 0 个")
        self._source_count.setObjectName("sourceCount")
        head.addWidget(heading)
        head.addStretch(1)
        head.addWidget(self._source_count)
        source_layout.addLayout(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("sourceScroll")
        source_inner = QWidget()
        source_inner.setObjectName("sourceInner")
        source_grid = QGridLayout(source_inner)
        source_grid.setContentsMargins(2, 2, 2, 2)
        source_grid.setVerticalSpacing(8)
        self._source_checks = {}
        for index, (name, label) in enumerate(PROVIDER_CHOICES):
            checkbox = QCheckBox(label)
            checkbox.toggled.connect(self._persist_source_selection)
            self._source_checks[name] = checkbox
            source_grid.addWidget(checkbox, index, 0)
        scroll.setWidget(source_inner)
        source_layout.addWidget(scroll, 1)
        pick_row = QHBoxLayout()
        pick_row.setSpacing(6)
        btn_all = QPushButton("全选")
        btn_none = QPushButton("全不选")
        btn_all.clicked.connect(lambda: self._set_all_sources(True))
        btn_none.clicked.connect(lambda: self._set_all_sources(False))
        pick_row.addWidget(btn_all)
        pick_row.addWidget(btn_none)
        pick_row.addStretch(1)
        source_layout.addLayout(pick_row)
        layout.addWidget(source_card, 1)

        out_card = QFrame()
        out_card.setObjectName("card")
        out_layout = QVBoxLayout(out_card)
        out_layout.setContentsMargins(14, 12, 14, 12)
        out_layout.setSpacing(8)
        out_title = QLabel("输出目录")
        out_title.setObjectName("cardTitle")
        out_layout.addWidget(out_title)
        self.use_custom_out = QCheckBox("输出到指定目录")
        self.use_custom_out.toggled.connect(self._toggle_output)
        out_layout.addWidget(self.use_custom_out)
        out_hint = QLabel("不勾选则在原目录处理")
        out_hint.setObjectName("hintNote")
        out_hint.setWordWrap(True)
        out_layout.addWidget(out_hint)
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("选择输出目录...")
        self.output_edit.setEnabled(False)
        out_layout.addWidget(self.output_edit)
        self.btn_out_browse = QPushButton("浏览")
        self.btn_out_browse.setEnabled(False)
        self.btn_out_browse.clicked.connect(self._browse_output)
        out_layout.addWidget(self.btn_out_browse)
        layout.addWidget(out_card)
        return rail

    def _show_sheet(self, index: int, nav: str = "work") -> None:
        self._sheets.setCurrentIndex(index)
        self._nav_work.setChecked(nav == "work")
        self._nav_log.setChecked(nav == "log")

    def _refresh_task_count(self) -> None:
        self._task_count.setText(f"已选 {self.input_list.count()} 个")

    def _refresh_kpis(self) -> None:
        counts = {WAITING: 0, RUNNING: 0, SUCCESS: 0, FAILED: 0}
        for row in range(self.result_table.rowCount()):
            badge = self.result_table.cellWidget(row, 8)
            if isinstance(badge, StatusBadge):
                status = badge.status() or WAITING
            else:
                item = self.result_table.item(row, 8)
                status = item.data(STATUS_ROLE) if item is not None else WAITING
            if status in counts:
                counts[status] += 1
        for key, label in self._kpi_labels.items():
            label.setText(str(counts.get(key, 0)))

    def _refresh_source_count(self) -> None:
        count = sum(1 for box in self._source_checks.values() if box.isChecked())
        self._source_count.setText(f"已选 {count} 个")

    def _set_row_status(self, row: int, status: str) -> None:
        item = self.result_table.item(row, 8)
        if item is not None:
            update_status_item(item, status)
        badge = self.result_table.cellWidget(row, 8)
        if isinstance(badge, StatusBadge):
            badge.set_status(status)

    def _build_menu(self) -> None:
        menubar = self.menuBar()

        file_menu = menubar.addMenu("文件(&F)")

        act_folders = QAction("选择文件夹", self)
        act_folders.triggered.connect(self._browse_folders)
        file_menu.addAction(act_folders)
        act_videos = QAction("选择视频", self)
        act_videos.triggered.connect(self._browse_videos)
        file_menu.addAction(act_videos)

        file_menu.addSeparator()

        act_settings = QAction("设置", self)
        act_settings.triggered.connect(self._open_settings)
        file_menu.addAction(act_settings)

        file_menu.addSeparator()

        act_exit = QAction("退出", self)
        act_exit.triggered.connect(self.close)
        file_menu.addAction(act_exit)

        help_menu = menubar.addMenu("帮助(&H)")
        act_about = QAction("关于", self)
        act_about.triggered.connect(self._show_about)
        help_menu.addAction(act_about)

    # ------------------------------------------------------------ theme

    def _sync_theme_combo(self) -> None:
        """让下拉框选中当前主题（不触发切换）。"""
        self.theme_combo.blockSignals(True)
        idx = self.theme_combo.findData(self.current_theme)
        if idx >= 0:
            self.theme_combo.setCurrentIndex(idx)
        self.theme_combo.blockSignals(False)

    def _on_theme_changed(self) -> None:
        theme = self.theme_combo.currentData()
        if not theme or theme == self.current_theme:
            return
        self.current_theme = apply_theme(theme)
        save_theme_to_config(self.config, theme)
        try:
            save_config(self.config, self.config_path)
        except OSError as exc:
            logger.warning("主题配置保存失败: %s", exc)
        self._append_log(f"已切换到{THEMES[theme]}主题。")

    # ------------------------------------------------------------ paths

    def _input_paths(self) -> list[str]:
        paths = []
        for row in range(self.input_list.count()):
            item = self.input_list.item(row)
            path = item.data(Qt.ItemDataRole.UserRole) or item.text()
            if path:
                paths.append(path)
        return paths

    def _add_input_paths(self, paths: list[str], *, reveal: bool = False) -> None:
        existing = {os.path.normcase(p) for p in self._input_paths()}
        added = False
        for raw in paths:
            path = os.path.normpath(raw)
            key = os.path.normcase(path)
            if key in existing or not os.path.exists(path):
                continue
            existing.add(key)
            label = path if os.path.isdir(path) else os.path.basename(path)
            item = QListWidgetItem(label)
            item.setToolTip(path)
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.input_list.addItem(item)
            added = True
        self._refresh_task_count()
        if reveal and added:
            self._show_sheet(0, "work")

    def _load_paths_from_config(self) -> None:
        """从配置恢复上次的输入/输出路径。"""
        rename = self.config.get("rename", {})
        saved = self.config.get("last_input_paths") or []
        if isinstance(saved, str):
            saved = [saved]
        if not saved:
            last_input = self.config.get("last_input_dir", "")
            saved = [last_input] if last_input else []
        self._add_input_paths([p for p in saved if isinstance(p, str)])
        custom_dir = rename.get("custom_output_dir", "")
        if rename.get("output_mode") == "custom_dir" and custom_dir:
            self.use_custom_out.setChecked(True)
            self.output_edit.setText(custom_dir)

    def _dialog_start(self) -> str:
        current = self._input_paths()
        if current:
            start = current[-1] if os.path.isdir(current[-1]) else os.path.dirname(current[-1])
        else:
            start = self.config.get("last_input_dir", "") or os.path.expanduser("~")
        return start if os.path.isdir(start) else os.path.expanduser("~")

    def _browse_folders(self) -> None:
        from gui.win_dialogs import pick_folders

        try:
            chosen = pick_folders(int(self.winId()), self._dialog_start())
        except OSError as exc:
            logger.warning("%s", exc)
            path = QFileDialog.getExistingDirectory(self, "选择文件夹", self._dialog_start())
            chosen = [path] if path else None
        if not chosen:
            return
        self._add_input_paths([path for path in chosen if os.path.isdir(path)], reveal=True)

    def _browse_videos(self) -> None:
        exts = self.config.get("scan", {}).get("video_extensions") or []
        if not exts:
            from core.parser import VIDEO_EXTENSIONS
            exts = sorted(VIDEO_EXTENSIONS)
        patterns = " ".join(
            f"*{ext if str(ext).startswith('.') else '.' + str(ext)}" for ext in exts
        )
        files, _selected = QFileDialog.getOpenFileNames(
            self,
            "选择视频",
            self._dialog_start(),
            f"视频文件 ({patterns})",
        )
        self._add_input_paths(files, reveal=True)

    def _remove_inputs(self) -> None:
        for item in self.input_list.selectedItems():
            self.input_list.takeItem(self.input_list.row(item))
        self._refresh_task_count()

    def _browse_output(self) -> None:
        current = self._input_paths()
        start = self.output_edit.text().strip() or (current[0] if current else "") or os.path.expanduser("~")
        if start and not os.path.isdir(start):
            start = os.path.dirname(start)
        path = QFileDialog.getExistingDirectory(self, "选择输出目录", start)
        if path:
            self.output_edit.setText(os.path.normpath(path))

    def _toggle_output(self, checked: bool) -> None:
        self.output_edit.setEnabled(checked)
        self.btn_out_browse.setEnabled(checked)

    def _persist_paths(self) -> None:
        """把当前输入/输出路径写回配置（下次启动恢复）。"""
        paths = self._input_paths()
        self.config["last_input_paths"] = paths
        anchor = paths[0] if paths else ""
        self.config["last_input_dir"] = anchor if os.path.isdir(anchor) else os.path.dirname(anchor)

        rename = self.config.setdefault("rename", {})
        if self.use_custom_out.isChecked():
            out_dir = self.output_edit.text().strip()
            rename["output_mode"] = "custom_dir"
            rename["custom_output_dir"] = out_dir
        else:
            rename["output_mode"] = "in_place"
            rename["custom_output_dir"] = ""

        try:
            save_config(self.config, self.config_path)
        except OSError as exc:
            logger.warning("路径配置保存失败: %s", exc)

    # ------------------------------------------------------------ actions

    def _validate_and_apply_paths(self) -> bool:
        paths = self._input_paths()
        if not paths:
            QMessageBox.warning(self, "提示", "请先选择视频文件或文件夹。")
            return False
        missing = [path for path in paths if not os.path.exists(path)]
        if missing:
            QMessageBox.warning(self, "提示", "这些路径不存在:\n" + "\n".join(missing[:8]))
            return False
        if self.use_custom_out.isChecked():
            out_dir = self.output_edit.text().strip()
            if not out_dir:
                QMessageBox.warning(self, "提示", "已勾选自定义输出，请选择输出路径。")
                return False
            if not os.path.isdir(out_dir):
                # 输出目录允许不存在，执行时会创建；这里仅确认父目录有效
                parent = os.path.dirname(out_dir)
                if parent and not os.path.isdir(parent):
                    QMessageBox.warning(self, "提示", f"输出路径的父目录不存在:\n{parent}")
                    return False
            out_key = os.path.normcase(os.path.abspath(out_dir))
            for path in paths:
                if os.path.isdir(path) and os.path.normcase(os.path.abspath(path)) == out_key:
                    QMessageBox.warning(self, "提示", "输出路径不能与选中的文件夹相同。")
                    return False
        self._persist_paths()
        return True

    def _sync_source_checks(self) -> None:
        enabled = {
            item.get("name"): item.get("enabled", False)
            for item in self.config.get("providers", [])
            if isinstance(item, dict)
        }
        for name, checkbox in self._source_checks.items():
            checkbox.blockSignals(True)
            checkbox.setChecked(bool(enabled.get(name, False)))
            checkbox.blockSignals(False)
        self._refresh_source_count()

    def _set_all_sources(self, checked: bool) -> None:
        for checkbox in self._source_checks.values():
            checkbox.blockSignals(True)
            checkbox.setChecked(checked)
            checkbox.blockSignals(False)
        self._persist_source_selection()

    def _selected_sources(self) -> list[str]:
        return [name for name, checkbox in self._source_checks.items() if checkbox.isChecked()]

    def _persist_source_selection(self, *_args) -> None:
        apply_provider_selection(self.config, set(self._selected_sources()))
        try:
            save_config(self.config, self.config_path)
        except OSError as exc:
            logger.warning("刮削源选择保存失败: %s", exc)
        self._refresh_source_count()

    def _on_scan(self) -> None:
        if not self._validate_and_apply_paths():
            return
        if not self._selected_sources():
            QMessageBox.warning(self, "提示", "请至少选择一个刮削源。")
            return
        if not self._proxy_gate():
            return
        if not self._fc2_gate():
            return
        self._start_worker(dry_run=True)

    def _on_apply(self) -> None:
        if not self._validate_and_apply_paths():
            return
        if not self._selected_sources():
            QMessageBox.warning(self, "提示", "请至少选择一个刮削源。")
            return
        if not self._proxy_gate():
            return
        confirm = QMessageBox.question(
            self,
            "确认执行",
            "将实际刮削并重命名/移动文件，此操作会改动磁盘文件。\n确定继续吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        if not self._fc2_gate():
            return
        self._start_worker(dry_run=False)

    def _proxy_gate(self) -> bool:
        """刮削前的代理门控。

        代理未启用时弹窗提示“刮削可能无法获取任何数据”，允许继续或中止，
        并在日志窗口输出红色警告。返回 True 表示继续，False 表示中止。
        这里只做本地配置判断（不发起网络请求，避免阻塞界面）；代理是否真正
        可用，在“设置-网络代理”里用“测试连接”验证。
        """
        from core.proxy import normalize_proxy_config

        raw_proxy = self.config.get("http", {}).get("proxy")
        cfg = normalize_proxy_config(raw_proxy)
        if cfg.get("enabled"):
            return True

        self._append_log(
            '<span style="color:#FF6B6B;font-weight:bold;">'
            "⚠ 当前未开启代理，刮削可能无法获取任何数据。</span>"
        )
        choice = QMessageBox.warning(
            self,
            "未开启代理",
            "当前未开启代理，刮削可能无法获取任何数据。\n"
            "建议先在「设置 - 网络代理」中开启并测试代理。\n\n"
            "是否仍要继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if choice != QMessageBox.Yes:
            self._append_log("已中止：未开启代理。")
            return False
        self._append_log(
            '<span style="color:#E0A83D;">用户选择在无代理情况下继续。</span>'
        )
        return True

    def _fc2_gate(self) -> bool:
        """含 FC2 番号时，先确认本机浏览器登录。取消则不开始刮削。"""
        if "fc2" not in self._selected_sources():
            return True
        from core.fc2_auth import find_fc2_article_id

        sources = self._input_paths()
        article_id = find_fc2_article_id(sources, self.config)
        if not article_id:
            return True
        if not ensure_fc2_login(self, article_id, self.config):
            self._append_log("已取消：FC2 尚未登录。")
            return False
        self._append_log("FC2 登录已就绪。")
        return True

    def _start_worker(self, dry_run: bool) -> None:
        sources = self._input_paths()

        self.progress_bar.setValue(0)
        self.result_table.setRowCount(0)
        self._refresh_kpis()
        self.plans = []

        self.worker = ScrapeWorker(sources, self.config_path, dry_run=dry_run)
        self.worker.progress_update.connect(self._on_progress)
        self.worker.finished_signal.connect(self._on_finished)
        self.worker.error_signal.connect(self._on_error)
        self.worker.log_signal.connect(self._append_log)
        self.worker.cancelled_signal.connect(self._on_cancelled)
        self.worker.file_list_signal.connect(self._init_rows)
        self.worker.file_progress_signal.connect(self._on_file_progress)
        self.worker.long_name_signal.connect(self._on_long_names)

        self._set_running(True)
        mode = "扫描预览" if dry_run else "执行刮削"
        picked = "、".join(
            label for name, label in PROVIDER_CHOICES if name in set(self._selected_sources())
        )
        self._append_log(f"===== 开始{mode}: {len(sources)} 个选择 =====")
        self._append_log(f"本次刮削源: {picked}")
        self.worker.start()

    def _on_long_names(self, plans: list) -> None:
        """工作线程刮削结束后、写入前弹出。必须把结果交回去，否则工作线程会一直等。"""
        from PySide6.QtWidgets import QDialog

        from gui.long_name_dialog import LongNameDialog

        choices = {}
        try:
            dialog = LongNameDialog(plans, self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                choices = dialog.choices()
                self._append_log(f"已确认 {len(choices)} 个超长文件名。")
            else:
                choices = None
                self._append_log("已取消：超长文件名未确认，这些文件保留在原位置。")
        except Exception:
            logger.exception("超长文件名窗口失败，改用自动截断名称")
            self._append_log("超长文件名窗口出错，已改用自动截断名称。")
            choices = {}
        if self.worker is not None:
            self.worker.provide_long_names(choices)

    def _set_running(self, running: bool) -> None:
        self.btn_scan.setEnabled(not running)
        self.btn_apply.setEnabled(not running)
        self.btn_cancel.setEnabled(running)
        for checkbox in self._source_checks.values():
            checkbox.setEnabled(not running)

    # ------------------------------------------------------ worker slots

    def _on_progress(self, info: dict) -> None:
        percent = info.get("percent", 0)
        self.progress_bar.setValue(int(percent))
        phase = info.get("phase", "")
        msg = info.get("message", "")
        self.status_label.setText(f"[{phase}] {msg}")

    def _on_finished(self, plans: list) -> None:
        self.plans = plans
        self._populate_table(plans)
        self._set_running(False)

        # 统计刮削失败（无元数据）的条目
        no_meta = [p for p in plans if getattr(p, "metadata", None) is None]
        if not plans:
            self.status_label.setText("未发现可处理的视频")
            self._append_log("未发现可处理的视频文件。")
            QMessageBox.information(
                self, "无结果",
                "没有发现可处理的视频文件。\n请确认目录正确，并在“扫描设置”中添加视频扩展名。",
            )
        elif len(no_meta) == len(plans):
            self.status_label.setText("刮削失败：未获取到任何信息")
            self._append_log("全部视频均未获取到刮削信息。")
            QMessageBox.warning(
                self, "刮削失败",
                "未能获取任何视频的刮削信息。\n\n可能原因：\n"
                "· 未开启代理或代理不可用（请在 设置-网络代理 中测试）；\n"
                "· 网络异常或目标站点无法访问；\n"
                "· 文件名中无有效番号，无法匹配。\n\n"
                "视频已保留在原位置，未做改动。",
            )
        elif no_meta:
            self.status_label.setText(
                f"完成 {len(plans) - len(no_meta)}/{len(plans)}，{len(no_meta)} 个无信息"
            )
            self._append_log(
                f"完成：{len(plans) - len(no_meta)} 个成功，"
                f"{len(no_meta)} 个未获取到信息（已保留原位）。"
            )
        else:
            self.status_label.setText(f"完成，共 {len(plans)} 条计划")
            self._append_log(f"完成，共 {len(plans)} 条计划。")

    def _on_cancel(self) -> None:
        """用户点击“取消/中断”：立即请求协作式停止，界面先恢复响应。"""
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.status_label.setText("正在中断…")
            self._append_log("正在中断，停止后续任务…")
            self.btn_cancel.setEnabled(False)

    def _on_cancelled(self, message: str) -> None:
        """worker 确认中断完成。"""
        self._set_running(False)
        self.status_label.setText("已中断")
        self._append_log(f"[中断] {message}")

    def _on_error(self, message: str) -> None:
        self._set_running(False)
        self.status_label.setText("出错")
        self._append_log(f"[错误] {message}")
        QMessageBox.critical(
            self, "程序错误",
            f"执行过程中发生错误：\n\n{message}\n\n"
            "请查看日志窗口获取详情；如与网络有关，请检查代理设置后重试。",
        )

    def _init_rows(self, files: list) -> None:
        """扫描+解析完成后立即建行：序号/原文件名/番号 + 进度条(等待) + 状态(等待中)。

        运行中逐行更新进度与状态；任务结束后 _populate_table 只补其余数据列，
        不重建行，避免进度/状态被清空。
        """
        self.result_table.setRowCount(len(files))
        for pos, finfo in enumerate(files):
            index = int(finfo.get("index", pos))
            if index < 0 or index >= self.result_table.rowCount():
                index = pos
            # 序号
            it_no = QTableWidgetItem(str(index + 1))
            it_no.setTextAlignment(Qt.AlignCenter)
            self.result_table.setItem(index, 0, it_no)
            # 原文件名
            it_file = QTableWidgetItem(finfo.get("file_name", ""))
            self.result_table.setItem(index, 1, it_file)
            # 番号
            it_code = QTableWidgetItem(finfo.get("code", "") or "")
            self.result_table.setItem(index, 2, it_code)
            # 处理进度：内嵌进度条，百分比由 setValue 驱动
            bar = RowProgressBar()
            self.result_table.setCellWidget(index, 7, bar)
            # 状态：保留数据项，界面用圆角标签
            self.result_table.setItem(index, 8, make_status_item(WAITING))
            self.result_table.setCellWidget(index, 8, StatusBadge(WAITING))
        self._refresh_kpis()
        self._show_sheet(0, "work")

    def _on_file_progress(self, payload: dict) -> None:
        """更新某一行的阶段进度与状态（仅在主线程执行，线程安全）。"""
        index = payload.get("index")
        if index is None:
            return
        index = int(index)
        if index < 0 or index >= self.result_table.rowCount():
            return
        stage = int(payload.get("stage", 0))
        status = payload.get("status", RUNNING)

        bar = self.result_table.cellWidget(index, 7)
        if isinstance(bar, RowProgressBar):
            # 进度只增不减（失败保持当前阶段，由颜色表达）
            value = stage if status == FAILED else max(stage, bar.value())
            bar.set_status(status, value)

        self._set_row_status(index, status)
        self._refresh_kpis()
        self.result_table.viewport().update()

    def _populate_table(self, plans: list) -> None:
        """任务结束后：按“解析序(行索引)”补齐 标题/年份/刮削源/评分，并纠正最终状态。

        不重建行：运行中已建立的进度条与状态保留。
        """
        # 源路径 -> 行索引（与解析序一致）
        path_to_row = {}
        for row in range(self.result_table.rowCount()):
            it = self.result_table.item(row, 1)
            if it is not None:
                path_to_row[it.text()] = row

        # 若运行中没有建行（例如文件列表事件缺失），则整体建一次
        if self.result_table.rowCount() != len(plans):
            self._init_rows([
                {"index": i,
                 "file_name": os.path.basename(getattr(pl, "source_path", "")),
                 "code": getattr(getattr(pl, "metadata", None), "code", "") or ""}
                for i, pl in enumerate(plans)
            ])

        for row, plan in enumerate(plans):
            meta = getattr(plan, "metadata", None)
            parsed_name = os.path.basename(getattr(plan, "source_path", ""))
            # 精确对齐到行索引
            target_row = path_to_row.get(parsed_name, row)

            # 序号 / 原文件名 / 番号 已存在，这里补 标题/年份/刮削源/评分
            data_cells = {
                3: getattr(meta, "title", "") or "",
                4: str(getattr(meta, "year", "") or ""),
                5: getattr(meta, "source_provider", "") or "",
                6: str(getattr(meta, "rating", "") or ""),
            }
            for col, text in data_cells.items():
                old = self.result_table.item(target_row, col)
                old_status = old.data(STATUS_ROLE) if old is not None else None
                item = QTableWidgetItem(text)
                if col in (4, 6):
                    item.setTextAlignment(Qt.AlignCenter)
                # 保留状态列以外的数据单元格不需要颜色
                self.result_table.setItem(target_row, col, item)

            # 根据最终元数据纠正状态与进度（以最终结果为准）
            bar = self.result_table.cellWidget(target_row, 7)
            st_item = self.result_table.item(target_row, 8)
            if getattr(plan, "apply_status", "") == "skipped":
                if isinstance(bar, RowProgressBar):
                    bar.set_status(SKIPPED, bar.value())
                self._set_row_status(target_row, SKIPPED)
            elif meta is None:
                # 无信息：可能是跳过，也可能之前已标失败；保留失败优先
                cur_status = st_item.data(STATUS_ROLE) if st_item else None
                if cur_status != FAILED:
                    if isinstance(bar, RowProgressBar):
                        bar.set_status(SKIPPED, 0)
                    self._set_row_status(target_row, SKIPPED)
            else:
                if isinstance(bar, RowProgressBar):
                    bar.set_status(SUCCESS, 100)
                self._set_row_status(target_row, SUCCESS)
        self._refresh_kpis()

    # ------------------------------------------------------------ other

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.config, self.config_path, self)
        if dialog.exec():
            self.config = ensure_known_providers(dialog.get_config())
            self._sync_source_checks()
            try:
                path = save_config(self.config, self.config_path)
                self._append_log(f"设置已保存: {path}")
            except OSError as exc:
                QMessageBox.warning(self, "保存失败", str(exc))

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "关于 MediaScraper",
            f"MediaScraper {__version__}\n家庭影院媒体刮削与重命名工具\n\n"
            "支持多 Provider 聚合刮削、中文标题优先、并行处理、\n"
            "NFO/JSON 元数据、封面下载与 Kodi 风格整理。",
        )

    def _append_log(self, text: str) -> None:
        self.log_view.append(text)

    def closeEvent(self, event) -> None:
        if self.worker and self.worker.isRunning():
            confirm = QMessageBox.question(
                self,
                "确认退出",
                "任务仍在运行，确定退出吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if confirm != QMessageBox.Yes:
                event.ignore()
                return
            self.worker.cancel()
            self.worker.wait(5000)
        event.accept()