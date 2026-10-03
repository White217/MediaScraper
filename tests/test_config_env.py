"""配置模板不含隐私，环境变量只覆盖对应字段。"""

import os
from pathlib import Path

import yaml

from core.config import apply_env_overrides


EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.yaml"


def test_example_config_has_no_secrets():
    text = EXAMPLE.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert "wushu" not in text.lower()
    assert "C:\\Users" not in text
    assert "C:/Users" not in text
    assert "7897" not in text

    keys = []
    for provider in data["providers"]:
        if "api_key" in provider:
            keys.append(provider["api_key"])
        if "api_id" in provider:
            keys.append(provider["api_id"])
        if "affiliate_id" in provider:
            keys.append(provider["affiliate_id"])
    assert keys
    assert all(value in ("", None) for value in keys)
    assert data["http"]["proxy"]["enabled"] is False
    assert data["http"]["proxy"]["username"] in ("", None)
    assert data["http"]["proxy"]["password"] in ("", None)
    assert data["rename"]["custom_output_dir"] in ("", None)
    assert "last_input_paths" not in data
    assert "last_input_dir" not in data


def test_env_overrides_fill_secrets_without_wiping_others(monkeypatch):
    for name in (
        "JAVINFO_API_KEY",
        "DMM_API_ID",
        "DMM_AFFILIATE_ID",
        "MEDIASCRAPER_PROXY_ENABLED",
        "MEDIASCRAPER_PROXY_TYPE",
        "MEDIASCRAPER_PROXY_HOST",
        "MEDIASCRAPER_PROXY_USERNAME",
        "MEDIASCRAPER_PROXY_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TMDB_API_KEY", "from-env")
    monkeypatch.setenv("MEDIASCRAPER_PROXY_PORT", "7890")
    monkeypatch.setenv("MEDIASCRAPER_OUTPUT_DIR", "D:/Media/out")

    config = apply_env_overrides({
        "providers": [
            {"name": "tmdb", "enabled": False, "api_key": ""},
            {"name": "javinfo", "enabled": False, "api_key": "keep-me"},
        ],
        "http": {"proxy": {"enabled": False, "port": 1, "host": "127.0.0.1"}},
        "rename": {"output_mode": "in_place", "custom_output_dir": ""},
    })

    by_name = {item["name"]: item for item in config["providers"]}
    assert by_name["tmdb"]["api_key"] == "from-env"
    assert by_name["tmdb"]["enabled"] is False
    assert by_name["javinfo"]["api_key"] == "keep-me"
    assert config["http"]["proxy"]["port"] == 7890
    assert config["http"]["proxy"]["enabled"] is False
    assert config["http"]["proxy"]["host"] == "127.0.0.1"
    assert config["rename"]["output_mode"] == "custom_dir"
    assert config["rename"]["custom_output_dir"] == "D:/Media/out"


def test_empty_env_does_not_replace_yaml(monkeypatch):
    for name in (
        "TMDB_API_KEY",
        "JAVINFO_API_KEY",
        "DMM_API_ID",
        "DMM_AFFILIATE_ID",
        "MEDIASCRAPER_PROXY_ENABLED",
        "MEDIASCRAPER_PROXY_HOST",
        "MEDIASCRAPER_PROXY_PORT",
        "MEDIASCRAPER_PROXY_USERNAME",
        "MEDIASCRAPER_PROXY_PASSWORD",
        "MEDIASCRAPER_OUTPUT_DIR",
    ):
        monkeypatch.delenv(name, raising=False)

    original = {
        "providers": [{"name": "tmdb", "enabled": True, "api_key": "local-key"}],
        "http": {"proxy": "http://127.0.0.1:7890"},
        "rename": {"output_mode": "in_place", "custom_output_dir": ""},
    }
    updated = apply_env_overrides(original)
    assert updated["providers"][0]["api_key"] == "local-key"
    assert updated["http"]["proxy"] == "http://127.0.0.1:7890"
    assert updated["rename"]["output_mode"] == "in_place"
    assert "TMDB_API_KEY" not in os.environ
