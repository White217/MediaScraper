# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置。

用法（在 media_scraper 目录下）:
    pyinstaller build_exe.spec --noconfirm --distpath ..

产物写到 --distpath 指定的目录（默认上一级目录的 MediaScraper.exe）。
桌面只保留指向该文件的快捷方式，不在桌面放 EXE。
"""

import os

block_cipher = None

# 项目根目录（spec 所在目录）
ROOT = os.path.abspath(".")

# 显式声明所有 provider / 子模块，确保被收集
hiddenimports = [
    "providers",
    "providers.base",
    "providers.registry",
    "providers.aggregator",
    "providers.http_client",
    "providers.javdb",
    "providers.javbus",
    "providers.javdatabase",
    "providers.javlibrary",
    "providers.javinfo",
    "providers.dmm",
    "providers.tmdb",
    "providers.mock",
    "providers.choices",
    "providers.net",
    "providers.page_parse",
    "providers.confidence",
    "providers.simple",
    "providers.r18dev",
    "providers.jav321",
    "providers.libredmm",
    "providers.avsox",
    "providers.javmenu",
    "providers.javtxt",
    "providers.javstore",
    "providers.fc2",
    "providers.heyzo",
    "providers.caribbeancom",
    "providers.tokyohot",
    "providers.maker_json",
    "providers.faleno",
    "providers.aventertainments",
    "core",
    "core.config",
    "core.app_paths",
    "core.http_sessions",
    "core.page_cache",
    "core.logging_setup",
    "core.orchestrator",
    "core.pipeline_apply",
    "core.pipeline_persist",
    "core.parser",
    "core.parts",
    "core.scanner",
    "core.models",
    "core.progress",
    "core.chinese_utils",
    "core.proxy",
    "core.fc2_auth",
    "aiohttp_socks",
    "aiohttp_socks.connector",
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    "PIL.ImageFile",
    "PIL.JpegImagePlugin",
    "PIL.PngImagePlugin",
    "PIL.GifImagePlugin",
    "PIL.WebPImagePlugin",
    "metadata",
    "metadata.nfo",
    "metadata.json_writer",
    "metadata.image",
    "rename",
    "rename.renamer",
    "rename.sanitizer",
    "gui",
    "gui.main_window",
    "gui.worker",
    "gui.settings_dialog",
    "gui.proxy_test_worker",
    "gui.fc2_login_dialog",
    "gui.long_name_dialog",
    "gui.theme",
    "gui.row_widgets",
    "gui.win_dialogs",
]

# 打包进去的数据文件（只读资源）。
# 只打入不含密钥的 config.example.yaml，首次运行再复制为 EXE 旁的 config.yaml。
# app.ico / 明暗 QSS 作为界面资源一并打入。
datas = [
    ("config.example.yaml", "."),
    ("assets/app.ico", "assets"),
    ("assets/dark.qss", "assets"),
    ("assets/light.qss", "assets"),
]

a = Analysis(
    ["app_gui.py"],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 排除用不到的大模块，减小体积
        "tkinter",
        "unittest",
        "pydoc",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="MediaScraper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # GUI 程序，不弹黑窗
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/app.ico",
    version="version_info.txt",
)
