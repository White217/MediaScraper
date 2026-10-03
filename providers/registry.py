"""
Provider 注册表
使用装饰器注册 Provider，支持按名称查找。
未启用的源等到 create_provider() 时才导入。
"""

import importlib
import logging
from typing import Dict, List, Optional, Type

from .base import BaseProvider

logger = logging.getLogger(__name__)

# 全局注册表
_REGISTRY: Dict[str, Type[BaseProvider]] = {}

# 名称 -> 模块。create_provider 时才 import，避免启动时加载全部源。
PROVIDER_MODULES: Dict[str, str] = {
    "mock": "providers.mock",
    "javbus": "providers.javbus",
    "dmm": "providers.dmm",
    "javinfo": "providers.javinfo",
    "javdatabase": "providers.javdatabase",
    "javlibrary": "providers.javlibrary",
    "javdb": "providers.javdb",
    "tmdb": "providers.tmdb",
    "r18dev": "providers.r18dev",
    "jav321": "providers.jav321",
    "libredmm": "providers.libredmm",
    "avsox": "providers.avsox",
    "javmenu": "providers.javmenu",
    "javtxt": "providers.javtxt",
    "javstore": "providers.javstore",
    "fc2": "providers.fc2",
    "heyzo": "providers.heyzo",
    "caribbeancom": "providers.caribbeancom",
    "tokyohot": "providers.tokyohot",
    "tenmusume": "providers.maker_json",
    "onepondo": "providers.maker_json",
    "pacopacomama": "providers.maker_json",
    "faleno": "providers.faleno",
    "aventertainments": "providers.aventertainments",
}


def _ensure_loaded(name: str) -> None:
    if name in _REGISTRY:
        return
    module_name = PROVIDER_MODULES.get(name)
    if not module_name:
        return
    try:
        importlib.import_module(module_name)
    except Exception:
        logger.exception("加载 Provider 模块失败: %s (%s)", name, module_name)


def register_provider(name_or_cls=None, *, name: Optional[str] = None):
    """
    装饰器：注册 Provider 类到全局注册表

    用法:
        @register_provider
        class MyProvider(BaseProvider):
            name = 'my_provider'

        @register_provider("custom_name")
        class MyProvider(BaseProvider):
            ...

        @register_provider(name="custom_name")
        class MyProvider(BaseProvider):
            ...
    """
    def _register(cls: Type[BaseProvider]) -> Type[BaseProvider]:
        # 确定 provider 名称
        if name:
            provider_name = name
        else:
            # 实例化获取 name 属性
            try:
                instance = cls()
                provider_name = instance.name
            except Exception:
                provider_name = getattr(cls, 'name', cls.__name__.lower())

        if provider_name in _REGISTRY:
            logger.warning(f"Provider '{provider_name}' 已注册，将被覆盖")

        _REGISTRY[provider_name] = cls
        logger.debug(f"已注册 Provider: {provider_name} ({cls.__name__})")
        return cls

    # 支持 @register_provider 不带参数的形式
    if isinstance(name_or_cls, type):
        return _register(name_or_cls)

    # 支持 @register_provider("name") 带参数的形式
    if isinstance(name_or_cls, str):
        name = name_or_cls

    return _register


def get_provider_class(name: str) -> Optional[Type[BaseProvider]]:
    """根据名称获取 Provider 类"""
    _ensure_loaded(name)
    return _REGISTRY.get(name)


def create_provider(name: str, **kwargs) -> Optional[BaseProvider]:
    """根据名称创建 Provider 实例"""
    cls = get_provider_class(name)
    if cls is None:
        logger.error(f"Provider '{name}' 未注册")
        return None
    try:
        return cls(**kwargs)
    except TypeError:
        # Provider 不接受额外参数（如 MockProvider），忽略 kwargs
        return cls()


def get_all_providers() -> Dict[str, Type[BaseProvider]]:
    """获取所有已注册的 Provider。命令行列出会加载全部源。"""
    for provider_name in PROVIDER_MODULES:
        _ensure_loaded(provider_name)
    return dict(_REGISTRY)


def list_provider_names() -> List[str]:
    """已知源名称（含尚未导入的）加上运行时动态注册的名称。"""
    names = list(PROVIDER_MODULES.keys())
    for registered in _REGISTRY:
        if registered not in names:
            names.append(registered)
    return names


def clear_registry() -> None:
    """清空注册表（测试用）"""
    _REGISTRY.clear()
