"""
Settings dialog for MediaScraper GUI.
"""

import os
from typing import Optional

from PySide6.QtWidgets import (
    QDialog, QFormLayout, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QLabel, QLineEdit, QComboBox, QCheckBox, QSpinBox, QDoubleSpinBox,
    QPushButton, QDialogButtonBox, QTabWidget, QWidget, QScrollArea,
)

from providers.choices import PROVIDER_CHOICES, ensure_known_providers

from core.config import save_config
from core.proxy import normalize_proxy_config
from gui.proxy_test_worker import ProxyTestWorker


class SettingsDialog(QDialog):
    """Settings configuration dialog."""

    def __init__(self, config: dict, config_path: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(500)
        self.setMinimumHeight(450)

        self._config = dict(config)  # Deep copy
        self._config_path = config_path
        self._proxy_worker: Optional[ProxyTestWorker] = None

        self._init_ui()
        self._load_config_to_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # Tab widget
        tabs = QTabWidget()

        # --- Providers tab ---
        providers_tab = QWidget()
        providers_layout = QVBoxLayout(providers_tab)

        # --- Network proxy ---
        proxy_group = QGroupBox("网络代理")
        proxy_grid = QGridLayout(proxy_group)

        self._proxy_enabled = QCheckBox("启用代理")
        proxy_grid.addWidget(self._proxy_enabled, 0, 0, 1, 4)

        proxy_grid.addWidget(QLabel("类型:"), 1, 0)
        self._proxy_type = QComboBox()
        self._proxy_type.addItems(["HTTP", "HTTPS", "SOCKS5"])
        proxy_grid.addWidget(self._proxy_type, 1, 1)

        proxy_grid.addWidget(QLabel("主机:"), 1, 2)
        self._proxy_host = QLineEdit()
        self._proxy_host.setPlaceholderText("127.0.0.1")
        proxy_grid.addWidget(self._proxy_host, 1, 3)

        proxy_grid.addWidget(QLabel("端口:"), 2, 0)
        self._proxy_port = QSpinBox()
        self._proxy_port.setRange(1, 65535)
        self._proxy_port.setValue(7890)
        proxy_grid.addWidget(self._proxy_port, 2, 1)

        proxy_grid.addWidget(QLabel("用户名:"), 2, 2)
        self._proxy_user = QLineEdit()
        self._proxy_user.setPlaceholderText("可选")
        proxy_grid.addWidget(self._proxy_user, 2, 3)

        proxy_grid.addWidget(QLabel("密码:"), 3, 0)
        self._proxy_pwd = QLineEdit()
        self._proxy_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self._proxy_pwd.setPlaceholderText("可选")
        proxy_grid.addWidget(self._proxy_pwd, 3, 1, 1, 3)

        proxy_grid.addWidget(QLabel("测试地址:"), 4, 0)
        self._proxy_test_url = QLineEdit()
        self._proxy_test_url.setPlaceholderText("https://www.gstatic.com/generate_204")
        proxy_grid.addWidget(self._proxy_test_url, 4, 1, 1, 3)

        # SOCKS5 DNS option
        self._proxy_remote_dns = QCheckBox("SOCKS5 时代由代理解析 DNS (socks5h)")
        self._proxy_remote_dns.setChecked(True)
        proxy_grid.addWidget(self._proxy_remote_dns, 5, 0, 1, 4)

        # Test button + status label
        test_row = QHBoxLayout()
        self._btn_test_proxy = QPushButton("测试代理连通性")
        self._btn_test_proxy.clicked.connect(self._on_test_proxy)
        test_row.addWidget(self._btn_test_proxy)
        self._proxy_status = QLabel("未测试")
        self._set_proxy_status("idle")
        test_row.addWidget(self._proxy_status, 1)
        proxy_grid.addLayout(test_row, 6, 0, 1, 4)

        # Compliance note
        note = QLabel(
            "提示：代理仅用于合法网络请求与正常网络环境适配，"
            "不得用于绕过验证码、登录限制、付费墙、DRM 或网站反爬风控。"
        )
        note.setWordWrap(True)
        note.setObjectName("hintNote")
        proxy_grid.addWidget(note, 7, 0, 1, 4)

        providers_layout.addWidget(proxy_group)

        # Enable/disable proxy fields based on checkbox
        self._proxy_enabled.toggled.connect(self._toggle_proxy_fields)
        self._toggle_proxy_fields(self._proxy_enabled.isChecked())

        # Provider priority
        provider_group = QGroupBox("刮削源 (按优先级排列)")
        provider_layout = QVBoxLayout(provider_group)

        self._provider_checks = {}
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(220)
        checks_host = QWidget()
        checks_layout = QVBoxLayout(checks_host)
        checks_layout.setContentsMargins(4, 4, 4, 4)
        for name, display in PROVIDER_CHOICES:
            cb = QCheckBox(display)
            self._provider_checks[name] = cb
            checks_layout.addWidget(cb)
        scroll.setWidget(checks_host)
        provider_layout.addWidget(scroll)

        providers_layout.addWidget(provider_group)
        providers_layout.addStretch()
        tabs.addTab(providers_tab, "刮削源")

        # --- Scraping tab ---
        scraping_tab = QWidget()
        scraping_layout = QVBoxLayout(scraping_tab)

        http_group = QGroupBox("HTTP 设置")
        http_form = QFormLayout(http_group)

        self._timeout_spin = QSpinBox()
        self._timeout_spin.setRange(5, 120)
        self._timeout_spin.setSuffix(" 秒")
        http_form.addRow("请求超时:", self._timeout_spin)

        self._delay_spin = QDoubleSpinBox()
        self._delay_spin.setRange(0.0, 10.0)
        self._delay_spin.setSingleStep(0.5)
        self._delay_spin.setSuffix(" 秒")
        http_form.addRow("请求间隔:", self._delay_spin)

        self._retries_spin = QSpinBox()
        self._retries_spin.setRange(0, 10)
        http_form.addRow("最大重试:", self._retries_spin)

        scraping_layout.addWidget(http_group)

        # Concurrency
        conc_group = QGroupBox("并发设置")
        conc_form = QFormLayout(conc_group)

        self._max_workers_spin = QSpinBox()
        self._max_workers_spin.setRange(1, 50)
        conc_form.addRow("最大并发数:", self._max_workers_spin)

        scraping_layout.addWidget(conc_group)
        scraping_layout.addStretch()
        tabs.addTab(scraping_tab, "刮削设置")

        # --- Rename tab ---
        rename_tab = QWidget()
        rename_layout = QVBoxLayout(rename_tab)

        template_group = QGroupBox("重命名模板")
        template_form = QFormLayout(template_group)

        self._tpl_movie = QLineEdit()
        self._tpl_movie.setPlaceholderText("{title} ({year}) [{resolution}]")
        template_form.addRow("电影:", self._tpl_movie)

        self._tpl_coded = QLineEdit()
        self._tpl_coded.setPlaceholderText("{code} {title}")
        template_form.addRow("番号:", self._tpl_coded)

        self._tpl_episode = QLineEdit()
        self._tpl_episode.setPlaceholderText("{title} S{season:02d}E{episode:02d}")
        template_form.addRow("剧集:", self._tpl_episode)

        rename_layout.addWidget(template_group)

        # Options
        opt_group = QGroupBox("选项")
        opt_layout = QVBoxLayout(opt_group)

        self._dry_run_check = QCheckBox("预览模式 (不实际移动文件)")
        opt_layout.addWidget(self._dry_run_check)

        self._subfolder_check = QCheckBox("为每个视频创建子文件夹")
        opt_layout.addWidget(self._subfolder_check)

        self._nfo_check = QCheckBox("生成 NFO 文件 (Kodi 兼容)")
        opt_layout.addWidget(self._nfo_check)

        self._json_check = QCheckBox("生成 metadata.json")
        opt_layout.addWidget(self._json_check)

        self._poster_check = QCheckBox("下载封面图")
        opt_layout.addWidget(self._poster_check)

        threshold_row = QHBoxLayout()
        threshold_row.addWidget(QLabel("置信度阈值"))
        self._confidence_spin = QSpinBox()
        self._confidence_spin.setRange(0, 100)
        self._confidence_spin.setValue(60)
        self._confidence_spin.setToolTip("低于该分数继续下一个源。0 表示有结果就停止。")
        threshold_row.addWidget(self._confidence_spin)
        threshold_row.addStretch()
        opt_layout.addLayout(threshold_row)

        rename_layout.addWidget(opt_group)
        rename_layout.addStretch()
        tabs.addTab(rename_tab, "重命名")

        # --- TMDB tab ---
        tmdb_tab = QWidget()
        tmdb_layout = QVBoxLayout(tmdb_tab)

        tmdb_group = QGroupBox("TMDB API 设置")
        tmdb_form = QFormLayout(tmdb_group)

        self._tmdb_key_input = QLineEdit()
        self._tmdb_key_input.setPlaceholderText("输入 TMDB API Key")
        tmdb_form.addRow("API Key:", self._tmdb_key_input)

        self._tmdb_lang = QComboBox()
        self._tmdb_lang.addItems(["zh-CN", "zh-TW", "en-US", "ja-JP"])
        tmdb_form.addRow("语言:", self._tmdb_lang)

        self._tmdb_region = QComboBox()
        self._tmdb_region.addItems(["CN", "TW", "US", "JP"])
        tmdb_form.addRow("地区:", self._tmdb_region)

        tmdb_layout.addWidget(tmdb_group)
        tmdb_layout.addStretch()
        tabs.addTab(tmdb_tab, "TMDB")

        # --- DMM / JavInfo tab ---
        paid_tab = QWidget()
        paid_layout = QVBoxLayout(paid_tab)

        # DMM
        dmm_group = QGroupBox("DMM/FANZA Affiliate API")
        dmm_form = QFormLayout(dmm_group)
        self._dmm_api_id = QLineEdit()
        self._dmm_api_id.setPlaceholderText("API ID")
        dmm_form.addRow("API ID:", self._dmm_api_id)
        self._dmm_aff_id = QLineEdit()
        self._dmm_aff_id.setPlaceholderText("Affiliate ID")
        dmm_form.addRow("Affiliate ID:", self._dmm_aff_id)
        self._dmm_floor = QComboBox()
        self._dmm_floor.addItems(["videoa", "anc"])
        dmm_form.addRow("内容分类:", self._dmm_floor)
        paid_layout.addWidget(dmm_group)

        # JavInfo
        ji_group = QGroupBox("JavInfo 聚合 API")
        ji_form = QFormLayout(ji_group)
        self._ji_key = QLineEdit()
        self._ji_key.setPlaceholderText("x-javinfo-key")
        ji_form.addRow("API Key:", self._ji_key)
        self._ji_sources = QLineEdit()
        self._ji_sources.setPlaceholderText("可选，如 fanza,javdb")
        ji_form.addRow("限定上游源:", self._ji_sources)
        paid_layout.addWidget(ji_group)

        paid_layout.addStretch()
        tabs.addTab(paid_tab, "DMM/JavInfo")

        layout.addWidget(tabs)

        # --- Dialog buttons ---
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel |
            QDialogButtonBox.StandardButton.Apply
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self._on_apply)
        layout.addWidget(buttons)

    def _load_config_to_ui(self):
        """Load current config values into UI widgets."""
        cfg = self._config

        # Proxy (structured, legacy string also supported)
        raw_proxy = cfg.get("http", {}).get("proxy", "")
        p = normalize_proxy_config(raw_proxy)
        self._proxy_enabled.setChecked(p["enabled"])
        self._proxy_type.setCurrentText(p["type"].upper())
        self._proxy_host.setText(p["host"])
        self._proxy_port.setValue(p["port"])
        self._proxy_user.setText(p["username"])
        self._proxy_pwd.setText(p["password"])
        self._proxy_test_url.setText(p["test_url"])
        self._proxy_remote_dns.setChecked(p.get("remote_dns", True))
        self._set_proxy_status("idle")

        # Providers
        for pconf in cfg.get("providers", []):
            name = pconf.get("name", "")
            if name in self._provider_checks:
                self._provider_checks[name].setChecked(pconf.get("enabled", True))

        # HTTP
        http = cfg.get("http", {})
        self._timeout_spin.setValue(http.get("timeout", 30))
        self._delay_spin.setValue(http.get("request_delay", 1.0))
        self._retries_spin.setValue(http.get("max_retries", 3))

        # Concurrency
        conc = cfg.get("concurrency", {})
        self._max_workers_spin.setValue(conc.get("max_workers", 10))

        # Rename templates
        templates = cfg.get("rename", {}).get("templates", {})
        self._tpl_movie.setText(templates.get("movie", ""))
        self._tpl_coded.setText(templates.get("coded", ""))
        self._tpl_episode.setText(templates.get("episode", ""))

        # Rename options
        rename = cfg.get("rename", {})
        self._dry_run_check.setChecked(rename.get("dry_run", True))

        meta = cfg.get("metadata", {})
        self._subfolder_check.setChecked(meta.get("create_subfolder", True))
        self._nfo_check.setChecked(meta.get("nfo", {}).get("enabled", True))
        self._json_check.setChecked(meta.get("json", {}).get("enabled", True))
        self._poster_check.setChecked(meta.get("images", {}).get("poster", True))
        try:
            threshold = int(meta.get("confidence_threshold", 60))
        except (TypeError, ValueError):
            threshold = 60
        self._confidence_spin.setValue(max(0, min(100, threshold)))

        # TMDB
        for pconf in cfg.get("providers", []):
            if pconf.get("name") == "tmdb":
                self._tmdb_key_input.setText(pconf.get("api_key", ""))
                self._tmdb_lang.setCurrentText(pconf.get("language", "zh-CN"))
                self._tmdb_region.setCurrentText(pconf.get("region", "CN"))
                break

        # DMM / JavInfo
        for pconf in cfg.get("providers", []):
            pname = pconf.get("name")
            if pname == "dmm":
                self._dmm_api_id.setText(pconf.get("api_id", ""))
                self._dmm_aff_id.setText(pconf.get("affiliate_id", ""))
                self._dmm_floor.setCurrentText(pconf.get("floor", "videoa"))
            elif pname == "javinfo":
                self._ji_key.setText(pconf.get("api_key", ""))
                self._ji_sources.setText(pconf.get("source_priority", ""))

    def _save_config_from_ui(self):
        """Save UI values back to config dict."""
        # Proxy (structured)
        self._config.setdefault("http", {})["proxy"] = {
            "enabled": self._proxy_enabled.isChecked(),
            "type": self._proxy_type.currentText().lower(),
            "host": self._proxy_host.text().strip(),
            "port": self._proxy_port.value(),
            "username": self._proxy_user.text().strip(),
            "password": self._proxy_pwd.text(),
            "test_url": self._proxy_test_url.text().strip()
            or "https://www.gstatic.com/generate_204",
            "remote_dns": self._proxy_remote_dns.isChecked(),
        }

        # Providers
        ensure_known_providers(self._config)
        providers = self._config.setdefault("providers", [])
        present = {p.get("name") for p in providers if isinstance(p, dict)}
        for name, checkbox in self._provider_checks.items():
            if name not in present:
                providers.append({"name": name, "enabled": checkbox.isChecked()})
        for pconf in providers:
            name = pconf.get("name", "")
            if name in self._provider_checks:
                pconf["enabled"] = self._provider_checks[name].isChecked()

        # HTTP
        http = self._config.setdefault("http", {})
        http["timeout"] = self._timeout_spin.value()
        http["request_delay"] = self._delay_spin.value()
        http["max_retries"] = self._retries_spin.value()

        # Concurrency
        conc = self._config.setdefault("concurrency", {})
        conc["max_workers"] = self._max_workers_spin.value()

        # Templates
        templates = self._config.setdefault("rename", {}).setdefault("templates", {})
        if self._tpl_movie.text():
            templates["movie"] = self._tpl_movie.text()
        if self._tpl_coded.text():
            templates["coded"] = self._tpl_coded.text()
        if self._tpl_episode.text():
            templates["episode"] = self._tpl_episode.text()

        # Rename options
        self._config.setdefault("rename", {})["dry_run"] = self._dry_run_check.isChecked()

        meta = self._config.setdefault("metadata", {})
        meta["create_subfolder"] = self._subfolder_check.isChecked()
        meta.setdefault("nfo", {})["enabled"] = self._nfo_check.isChecked()
        meta.setdefault("json", {})["enabled"] = self._json_check.isChecked()
        meta.setdefault("images", {})["poster"] = self._poster_check.isChecked()
        meta["confidence_threshold"] = self._confidence_spin.value()

        # TMDB
        for pconf in self._config.get("providers", []):
            if pconf.get("name") == "tmdb":
                pconf["api_key"] = self._tmdb_key_input.text().strip()
                pconf["language"] = self._tmdb_lang.currentText()
                pconf["region"] = self._tmdb_region.currentText()
                break

        # DMM / JavInfo
        for pconf in self._config.get("providers", []):
            pname = pconf.get("name")
            if pname == "dmm":
                pconf["api_id"] = self._dmm_api_id.text().strip()
                pconf["affiliate_id"] = self._dmm_aff_id.text().strip()
                pconf["floor"] = self._dmm_floor.currentText()
            elif pname == "javinfo":
                pconf["api_key"] = self._ji_key.text().strip()
                src = self._ji_sources.text().strip()
                if src:
                    pconf["source_priority"] = src

    # ── 网络代理 ──

    def _toggle_proxy_fields(self, enabled: bool) -> None:
        """根据启用开关启用/禁用代理配置字段。"""
        for w in (
            self._proxy_type, self._proxy_host, self._proxy_port,
            self._proxy_user, self._proxy_pwd, self._proxy_test_url,
            self._proxy_remote_dns,
        ):
            w.setEnabled(enabled)

    def _set_proxy_status(self, state: str, text: Optional[str] = None) -> None:
        """设置代理状态标签及颜色。state: idle/success/fail/testing。"""
        styles = {
            "idle": ("未测试", "#888"),
            "testing": ("正在测试…", "#0066cc"),
            "success": ("连接成功", "#1a7f37"),
            "fail": ("连接失败", "#cf222e"),
        }
        label, color = styles.get(state, styles["idle"])
        self._proxy_status.setText(text or label)
        self._proxy_status.setStyleSheet(f"color: {color}; font-weight: bold;")

    def _collect_proxy_config(self) -> dict:
        """从当前界面收集代理配置（不依赖已保存的 config）。"""
        return {
            "enabled": self._proxy_enabled.isChecked(),
            "type": self._proxy_type.currentText().lower(),
            "host": self._proxy_host.text().strip(),
            "port": self._proxy_port.value(),
            "username": self._proxy_user.text().strip(),
            "password": self._proxy_pwd.text(),
            "test_url": self._proxy_test_url.text().strip(),
            "remote_dns": self._proxy_remote_dns.isChecked(),
        }

    def _on_test_proxy(self) -> None:
        """触发代理连通性测试（后台线程）。"""
        if self._proxy_worker is not None and self._proxy_worker.isRunning():
            return

        proxy_cfg = self._collect_proxy_config()
        test_url = proxy_cfg.get("test_url") or None

        # 启用代理但主机为空，直接提示
        if proxy_cfg["enabled"] and not proxy_cfg["host"]:
            self._set_proxy_status("fail", "请填写代理主机")
            return

        self._btn_test_proxy.setEnabled(False)
        self._set_proxy_status("testing")

        self._proxy_worker = ProxyTestWorker(proxy_cfg, test_url=test_url, timeout=5.0)
        self._proxy_worker.result_signal.connect(self._on_proxy_result)
        self._proxy_worker.finished.connect(self._on_proxy_thread_finished)
        self._proxy_worker.start()

    def _on_proxy_result(self, result: dict) -> None:
        """处理代理测试结果。"""
        import logging

        log = logging.getLogger("media_scraper.gui")
        if result.get("ok"):
            self._set_proxy_status("success", result.get("message"))
            log.info("[代理测试] %s", result.get("message"))
        else:
            self._set_proxy_status("fail", result.get("message"))
            log.warning(
                "[代理测试] 失败 (status=%s): %s",
                result.get("status"), result.get("message"),
            )

    def _on_proxy_thread_finished(self) -> None:
        """测试线程结束，恢复按钮。"""
        self._btn_test_proxy.setEnabled(True)
        self._proxy_worker = None

    def _on_apply(self):
        """Apply settings without closing dialog."""
        self._save_config_from_ui()
        try:
            save_config(self._config, self._config_path)
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "保存失败", f"无法保存配置:\n{e}")

    def _on_save(self):
        """Save settings and close dialog."""
        self._on_apply()
        self.accept()

    def get_config(self) -> dict:
        """返回对话框里的配置（保存后由主窗口继续写盘）。"""
        return self._config
