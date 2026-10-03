"""
providers — 刮削源插件层

具体站点模块按需导入，避免启动时加载全部源。
"""

from .aggregator import ProviderAggregator
from .base import BaseProvider
from .registry import (
    create_provider,
    get_all_providers,
    list_provider_names,
    register_provider,
)

# 测试和 dry-run 常用 mock，体积很小，始终注册
from . import mock  # noqa: F401

__all__ = [
    "BaseProvider",
    "ProviderAggregator",
    "register_provider",
    "create_provider",
    "get_all_providers",
    "list_provider_names",
]
