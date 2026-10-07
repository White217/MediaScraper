"""
配置加载 / 保存 / 校验
支持 YAML 配置文件，合并默认值。
在 PyInstaller 打包环境下，配置存放在 EXE 旁的可写目录。
"""

import logging
import os
import shutil
import sys
from typing import Any, Optional

import yaml

from core.app_paths import config_file_path, resource_path

logger = logging.getLogger(__name__)


# 默认配置（保持嵌套结构，供各模块读取）
DEFAULT_CONFIG: dict[str, Any] = {
    "dry_run": True,
    "ui": {
        "theme": "light",  # 与 gui/theme.py DEFAULT_THEME、出厂亮色青绿主题一致
    },
    "scan": {
        "video_extensions": [
            ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
            ".mpg", ".mpeg", ".ts", ".iso",
        ],
        "exclude_dirs": [
            "subs", "subtitles", "sample", "extras", "behind the scenes",
            ".git",
        ],
        "scan_subdirs": True,
        "recursive": True,
        "follow_symlinks": False,
        "min_file_size_mb": 10,
    },
    "parser": {
        "prefer_chinese": True,
        "year_detection": True,
        "resolution_detection": True,
        "season_episode_detection": True,
    },
    "providers": [
        {"name": "javbus", "enabled": True, "priority": 1},
        {"name": "javdb", "enabled": True, "priority": 2},
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
        {"name": "tmdb", "enabled": True, "priority": 3,
         "api_key": "", "language": "zh-CN", "region": "CN"},
        {"name": "mock", "enabled": False, "priority": 99},
    ],
    "http": {
        "timeout": 30,
        "max_retries": 3,
        "request_delay": 0.3,
        "request_jitter": 0.6,
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "proxy": {
            "enabled": False,
            "type": "http",
            "host": "127.0.0.1",
            "port": 7890,
            "username": "",
            "password": "",
            "test_url": "https://www.gstatic.com/generate_204",
            "remote_dns": True,
        },
        "cache_dir": "cache",
        "cache_enabled": True,
        "cache_ttl": 86400,
    },
    "metadata": {
        "output_mode": "alongside",
        "create_subfolder": True,
        "download_posters": True,
        "download_backdrops": False,
        "nfo": {"enabled": True, "format": "kodi"},
        "json": {"enabled": True},
        "complete_title": True,
        # 低于该分数则继续下一个源。0 表示拿到任意结果就停止。
        "confidence_threshold": 60,
    },
    "rename": {
        "dry_run": True,
        "templates": {
            "movie": "{title} ({year}) [{resolution}]",
            "tv": "{title} S{season:02d}E{episode:02d}",
            "coded": "{code} {title}",
            "fallback": "{title}",
        },
        "output_mode": "in_place",
        "custom_output_dir": "",
        "conflict_resolution": "auto_suffix",
        "merge_parts": True,
        "conflict_strategy": "rename",
        "sanitize": True,
        "max_filename_length": 120,
    },
    "concurrency": {
        "max_workers": 10,
        "batch_size": 10,
        "rate_limit_per_minute": 60,
    },
    "logging": {
        "level": "INFO",
        "log_to_file": True,
        "log_dir": "logs",
    },
}


def deep_merge(base: dict, override: dict) -> dict:
    """递归合并两个字典，override 覆盖 base。"""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def get_enabled_providers(config: dict[str, Any]) -> list[dict[str, Any]]:
    """返回配置中所有 enabled=True 的 provider（保持原顺序）。"""
    providers = config.get("providers", [])
    if not isinstance(providers, list):
        return []
    return [p for p in providers if isinstance(p, dict) and p.get("enabled")]


def _write_initial_config(target: str) -> None:
    """在 target 还不存在时写入一份可编辑的初始配置。

    优先复制 config.example.yaml（无密钥、无本机路径）。
    兼容旧安装包里打进去的 config.yaml。两者都没有时，用代码内默认值生成。
    """
    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    # 开发环境下旁边的 config.yaml 是用户真配置，不能拿来当模板。
    names = ["config.example.yaml"]
    if getattr(sys, "frozen", False):
        names.append("config.yaml")
    for name in names:
        bundled = resource_path(name)
        if not os.path.isfile(bundled):
            continue
        if os.path.abspath(bundled) == os.path.abspath(target):
            continue
        shutil.copy2(bundled, target)
        logger.info("已从 %s 初始化配置文件", name)
        return

    with open(target, "w", encoding="utf-8") as handle:
        yaml.safe_dump(DEFAULT_CONFIG, handle, allow_unicode=True, sort_keys=False)
    logger.info("已由默认值生成配置文件")


def _ensure_user_config() -> str:
    """确保可写目录下存在 config.yaml。

    已有配置一律保留。只有首次运行才从模板复制。

    Returns:
        配置文件路径。
    """
    target = config_file_path()
    if os.path.exists(target):
        return target
    try:
        _write_initial_config(target)
    except OSError as exc:
        logger.warning("无法写入配置文件: %s", exc)
    return target


def _load_dotenv_near(config_path: str) -> None:
    """读取配置文件同目录的 .env。已存在的环境变量优先，不覆盖。"""
    env_path = os.path.join(os.path.dirname(os.path.abspath(config_path)), ".env")
    if not os.path.isfile(env_path):
        return
    try:
        with open(env_path, encoding="utf-8-sig") as handle:
            lines = handle.readlines()
    except OSError as exc:
        logger.warning("无法读取 .env: %s", exc)
        return

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def _env_value(name: str) -> str:
    return os.getenv(name, "").strip()


def _ensure_provider(config: dict[str, Any], name: str) -> dict[str, Any]:
    providers = config.get("providers")
    if not isinstance(providers, list):
        providers = []
        config["providers"] = providers
    for entry in providers:
        if isinstance(entry, dict) and entry.get("name") == name:
            return entry
    entry: dict[str, Any] = {"name": name, "enabled": False}
    providers.append(entry)
    return entry


def apply_env_overrides(config: dict[str, Any]) -> dict[str, Any]:
    """用环境变量覆盖密钥、代理和输出目录。空变量不改 YAML 里的值。"""
    if _env_value("TMDB_API_KEY"):
        _ensure_provider(config, "tmdb")["api_key"] = _env_value("TMDB_API_KEY")
    if _env_value("JAVINFO_API_KEY"):
        _ensure_provider(config, "javinfo")["api_key"] = _env_value("JAVINFO_API_KEY")
    if _env_value("DMM_API_ID"):
        _ensure_provider(config, "dmm")["api_id"] = _env_value("DMM_API_ID")
    if _env_value("DMM_AFFILIATE_ID"):
        _ensure_provider(config, "dmm")["affiliate_id"] = _env_value("DMM_AFFILIATE_ID")

    enabled = _env_value("MEDIASCRAPER_PROXY_ENABLED").lower()
    proxy_type = _env_value("MEDIASCRAPER_PROXY_TYPE").lower()
    host = _env_value("MEDIASCRAPER_PROXY_HOST")
    port_text = _env_value("MEDIASCRAPER_PROXY_PORT")
    username = os.getenv("MEDIASCRAPER_PROXY_USERNAME")
    password = os.getenv("MEDIASCRAPER_PROXY_PASSWORD")
    proxy_touched = any((
        enabled,
        proxy_type,
        host,
        port_text,
        username not in (None, ""),
        password not in (None, ""),
    ))
    if proxy_touched:
        http = config.setdefault("http", {})
        proxy = http.get("proxy")
        if not isinstance(proxy, dict):
            proxy = {
                "enabled": False,
                "type": "http",
                "host": "127.0.0.1",
                "port": 7890,
                "username": "",
                "password": "",
                "test_url": "https://www.gstatic.com/generate_204",
                "remote_dns": True,
            }
        if enabled in ("1", "true", "yes", "on"):
            proxy["enabled"] = True
        elif enabled in ("0", "false", "no", "off"):
            proxy["enabled"] = False
        if proxy_type:
            proxy["type"] = proxy_type
        if host:
            proxy["host"] = host
        if port_text.isdigit():
            proxy["port"] = int(port_text)
        if username not in (None, ""):
            proxy["username"] = username.strip()
        if password not in (None, ""):
            proxy["password"] = password
        http["proxy"] = proxy

    output_dir = _env_value("MEDIASCRAPER_OUTPUT_DIR")
    if output_dir:
        rename = config.setdefault("rename", {})
        rename["custom_output_dir"] = output_dir
        rename["output_mode"] = "custom_dir"
    return config


def _finalize(config: dict[str, Any]) -> dict[str, Any]:
    from providers.choices import ensure_known_providers
    return apply_env_overrides(ensure_known_providers(config))


def load_config(path: Optional[str] = None) -> dict[str, Any]:
    """加载配置文件并与默认值合并。

    Args:
        path: 配置文件路径。None 时使用应用可写目录下的 config.yaml；
              文件不存在则自动初始化。

    Returns:
        合并后的配置字典。
    """
    explicit_path = path is not None
    if path is None:
        path = _ensure_user_config()
    _load_dotenv_near(path)

    if not os.path.exists(path):
        if explicit_path:
            try:
                _write_initial_config(path)
            except OSError as exc:
                logger.warning("无法创建配置文件: %s", exc)
        else:
            logger.warning("配置文件不存在，使用默认配置")
            return _finalize(dict(DEFAULT_CONFIG))

    if not os.path.exists(path):
        return _finalize(dict(DEFAULT_CONFIG))

    try:
        with open(path, "r", encoding="utf-8") as f:
            user_config = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        logger.error("配置文件解析失败: %s，使用默认配置", exc)
        return _finalize(dict(DEFAULT_CONFIG))
    except OSError as exc:
        logger.error("无法读取配置文件: %s，使用默认配置", exc)
        return _finalize(dict(DEFAULT_CONFIG))

    merged = deep_merge(DEFAULT_CONFIG, user_config)
    logger.debug("配置已加载")
    return _finalize(merged)


def save_config(config: dict[str, Any], path: Optional[str] = None) -> str:
    """保存配置到 YAML 文件。

    Args:
        config: 配置字典。
        path:   目标路径。None 时保存到应用可写目录下的 config.yaml。

    Returns:
        实际保存路径。
    """
    if path is None:
        path = config_file_path()

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

    logger.info("配置已保存: %s", path)
    return path


def validate_config(config: dict[str, Any]) -> list[str]:
    """校验配置，返回问题列表（空列表表示通过）。"""
    issues: list[str] = []

    rename = config.get("rename", {})
    if rename.get("output_mode") not in ("in_place", "custom_dir"):
        issues.append("rename.output_mode 必须是 'in_place' 或 'custom_dir'")

    if rename.get("output_mode") == "custom_dir" and not rename.get("custom_output_dir"):
        issues.append("output_mode 为 custom_dir 时必须设置 custom_output_dir")

    max_len = rename.get("max_filename_length", 120)
    if not isinstance(max_len, int) or max_len < 20 or max_len > 240:
        issues.append("rename.max_filename_length 必须在 20-240 之间")

    # 兼容旧字段名 conflict_resolution / conflict_strategy
    valid_conflicts = ("auto_suffix", "rename", "skip", "overwrite")
    if ("conflict_resolution" in rename
            and rename["conflict_resolution"] not in valid_conflicts):
        issues.append("rename.conflict_resolution 值无效")
    if ("conflict_strategy" in rename
            and rename["conflict_strategy"] not in valid_conflicts):
        issues.append("rename.conflict_strategy 值无效")

    http = config.get("http", {})
    delay = http.get("request_delay", 1.0)
    if not isinstance(delay, (int, float)) or delay < 0:
        issues.append("http.request_delay 必须为非负数")

    providers = config.get("providers", [])
    if not isinstance(providers, list):
        issues.append("providers 必须是列表")
    elif not get_enabled_providers(config):
        issues.append("至少需要启用一个 provider")

    return issues
