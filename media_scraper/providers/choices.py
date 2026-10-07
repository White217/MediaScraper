"""刮削源在界面上的显示名，以及新源的默认配置。

新源默认关闭。主界面勾选后才会在当次刮削里启用，并写回配置。
"""

from __future__ import annotations

# (配置名, 界面短名)。顺序即主界面和设置页的排列顺序。
PROVIDER_CHOICES: list[tuple[str, str]] = [
    ("javdb", "JavDB"),
    ("javdatabase", "JavDatabase"),
    ("javlibrary", "JavLibrary"),
    ("javbus", "JavBus"),
    ("r18dev", "R18.dev"),
    ("jav321", "Jav321"),
    ("libredmm", "LibreDMM"),
    ("avsox", "AVSOX"),
    ("javmenu", "JavMenu"),
    ("javtxt", "JavTXT"),
    ("javstore", "JavStore"),
    ("fc2", "FC2"),
    ("heyzo", "HEYZO"),
    ("caribbeancom", "Caribbeancom"),
    ("tokyohot", "Tokyo-Hot"),
    ("tenmusume", "10musume"),
    ("onepondo", "1pondo"),
    ("pacopacomama", "Pacopacomama"),
    ("faleno", "Faleno"),
    ("aventertainments", "AV Entertainment"),
    ("dmm", "DMM/FANZA"),
    ("javinfo", "JavInfo"),
    ("tmdb", "TMDB"),
    ("mock", "Mock"),
]

# 探测通过后新增的源。插入到 tmdb / mock 之前，默认不启用。
NEW_PROVIDER_DEFAULTS: list[dict] = [
    {"name": "r18dev", "enabled": False, "priority": 20},
    {"name": "jav321", "enabled": False, "priority": 21},
    {"name": "libredmm", "enabled": False, "priority": 22},
    {"name": "avsox", "enabled": False, "priority": 23},
    {"name": "javmenu", "enabled": False, "priority": 24},
    {"name": "javtxt", "enabled": False, "priority": 25},
    {"name": "javstore", "enabled": False, "priority": 26},
    {"name": "fc2", "enabled": False, "priority": 27},
    {"name": "heyzo", "enabled": False, "priority": 28},
    {"name": "caribbeancom", "enabled": False, "priority": 29},
    {"name": "tokyohot", "enabled": False, "priority": 30},
    {"name": "tenmusume", "enabled": False, "priority": 31},
    {"name": "onepondo", "enabled": False, "priority": 32},
    {"name": "pacopacomama", "enabled": False, "priority": 33},
    {"name": "faleno", "enabled": False, "priority": 34},
    {"name": "aventertainments", "enabled": False, "priority": 35},
]

_CHOICE_NAMES = {name for name, _label in PROVIDER_CHOICES}
_INSERT_BEFORE = ("tmdb", "mock")


def ensure_known_providers(config: dict) -> dict:
    """把缺失的新源补进配置，不改已有条目的 enabled。"""
    providers = [p for p in config.get("providers", []) if isinstance(p, dict)]
    present = {p.get("name") for p in providers}
    insert_at = len(providers)
    for index, entry in enumerate(providers):
        if entry.get("name") in _INSERT_BEFORE:
            insert_at = index
            break
    for offset, entry in enumerate(item for item in NEW_PROVIDER_DEFAULTS if item["name"] not in present):
        providers.insert(insert_at + offset, dict(entry))
    config["providers"] = providers
    return config


def apply_provider_selection(config: dict, selected: set[str]) -> dict:
    """按勾选结果更新各源 enabled。凭证字段保持不变。"""
    ensure_known_providers(config)
    providers = config.setdefault("providers", [])
    present = {p.get("name") for p in providers if isinstance(p, dict)}
    for name in _CHOICE_NAMES:
        if name not in present:
            providers.append({"name": name, "enabled": name in selected})
    for entry in providers:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if name in _CHOICE_NAMES:
            entry["enabled"] = name in selected
    return config
