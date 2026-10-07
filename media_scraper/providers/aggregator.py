"""
Provider 聚合器
按配置顺序接续刮削。置信度不够、或本源无结果时，继续下一个源。
最终只锁定一个主源，空字段才允许从同一作品的其它结果补上。
"""

import copy
import logging
from typing import Dict, List, Optional

from core.chinese_utils import has_cjk
from core.models import Metadata, ParsedFilename, ProviderResult
from providers.base import BaseProvider
from providers.confidence import (
    COMPLETE_TITLE,
    choose_primary,
    cjk_title_candidate,
    classify_failure,
    fill_empty_fields,
    matches_file_code,
    meets_threshold,
    score_metadata,
    title_quality,
)
from providers.javdb import is_login_placeholder
from providers.page_parse import usable_poster_url
from providers.r18dev import is_standard_code
from providers.registry import create_provider

logger = logging.getLogger(__name__)


class ProviderAggregator:
    """
    聚合多个 Provider，按列表顺序作为优先级（越靠前越高）。

    流程:
    1. 根据配置中的 provider 列表按顺序创建 Provider 实例
    2. 对每个文件依次查询，结果统一封装为 ProviderResult
    3. 超时、404、空结果继续下一个源
    4. 命中后打分；低于阈值则保留候选并继续
    5. 出现高置信度结果，或全部源结束时，锁定得分最高的主源
    """

    def __init__(self, config: dict):
        self._config = config
        self._providers: List[BaseProvider] = []
        self._title_source: Optional[BaseProvider] = None
        self._init_providers()

    def _init_providers(self) -> None:
        """根据配置初始化 Provider 列表"""
        from core.proxy import build_proxy_url, make_connector, normalize_proxy_config

        provider_configs = self._config.get("providers", [])
        raw_proxy = self._config.get("http", {}).get("proxy", "")
        proxy_cfg = normalize_proxy_config(raw_proxy)
        proxy_url = build_proxy_url(proxy_cfg)

        # SOCKS5 需要连接器；HTTP/HTTPS 用 URL 透传
        socks_connector = None
        if proxy_url and proxy_cfg["type"] == "socks5":
            socks_connector = make_connector(proxy_cfg)

        for pconf in provider_configs:
            name = pconf.get("name", "")
            enabled = pconf.get("enabled", True)
            if not enabled:
                continue

            # Build kwargs from provider config
            kwargs: Dict[str, object] = {}
            if proxy_url:
                kwargs["proxy"] = proxy_url
            if socks_connector is not None:
                kwargs["connector"] = socks_connector
            # Pass through known provider parameters
            for key in (
                "api_key",
                "api_id",
                "affiliate_id",
                "language",
                "region",
                "base_url",
                "floor",
                "source_priority",
            ):
                if key in pconf:
                    kwargs[key] = pconf[key]

            provider = create_provider(name, **kwargs)
            if provider:
                self._tune_provider(provider)
                self._providers.append(provider)
                logger.info(f"已加载 Provider: {provider}")
            else:
                logger.warning(f"Provider '{name}' 创建失败，跳过")

        self._title_source = next(
            (provider for provider in self._providers if provider.name == "r18dev"),
            None,
        )
        if self._title_source is None:
            title_kwargs: Dict[str, object] = {}
            if proxy_url:
                title_kwargs["proxy"] = proxy_url
            if socks_connector is not None:
                title_kwargs["connector"] = socks_connector
            self._title_source = create_provider("r18dev", **title_kwargs)
            if self._title_source:
                self._tune_provider(self._title_source)

    def _tune_provider(self, provider: BaseProvider) -> None:
        """注入限速和自定义 UA。标题补充源与正式源使用同一套设置。"""
        delay = self._config.get("http", {}).get("request_delay", 0.3)
        try:
            provider.request_delay = float(delay)
        except (TypeError, ValueError):
            provider.request_delay = 0.3
        jitter = self._config.get("http", {}).get("request_jitter", 0.6)
        try:
            provider.request_jitter = float(jitter)
        except (TypeError, ValueError):
            provider.request_jitter = 0.6
        ua = (self._config.get("http", {}).get("user_agent") or "").strip()
        provider._user_agent = ua or None

    @property
    def providers(self) -> List[BaseProvider]:
        """当前已启用的 Provider 列表（按优先级排序）"""
        return list(self._providers)

    def _confidence_threshold(self) -> int:
        raw = self._config.get("metadata", {}).get("confidence_threshold", 60)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return 60
        return max(0, min(100, value))

    def _provider_chain(self, parsed: ParsedFilename) -> List[BaseProvider]:
        """可处理当前文件的源。FC2 番号时官方 fc2 提到链首。"""
        chain = [provider for provider in self._providers if provider.can_handle(parsed)]
        from providers.fc2 import fc2_article_id

        if not fc2_article_id(parsed):
            return chain
        official = [provider for provider in chain if provider.name == "fc2"]
        if not official:
            return chain
        others = [provider for provider in chain if provider.name != "fc2"]
        return official + others

    async def scrape(self, parsed: ParsedFilename) -> Optional[Metadata]:
        """
        按优先级接续刮削。

        Args:
            parsed: 文件名解析结果

        Returns:
            锁定主源后的元数据。全部失败返回 None。
        """
        chain = self._provider_chain(parsed)
        collected: List[ProviderResult] = []
        threshold = self._confidence_threshold()

        for index, provider in enumerate(chain):
            nxt = chain[index + 1].name if index + 1 < len(chain) else None
            result = await self._query_provider(provider, parsed)
            if not result.ok or result.metadata is None:
                self._log_miss(provider.name, result.reason or "空结果", nxt)
                continue

            score, quality = score_metadata(result.metadata, parsed)
            result.confidence = score
            result.title_quality = quality
            collected.append(result)
            if self._can_finish(collected, parsed, threshold):
                break

            title = (result.metadata.title or "").strip()
            if (
                provider.name != "mock"
                and meets_threshold(score, quality, threshold)
                and matches_file_code(result.metadata, parsed)
                and not has_cjk(title)
            ):
                if nxt:
                    logger.info(
                        "源 %s 命中，置信度 %d，标题为英文，继续查询源 %s",
                        provider.name,
                        score,
                        nxt,
                    )
                else:
                    logger.info("源 %s 命中，置信度 %d，标题为英文", provider.name, score)
                continue

            if nxt:
                logger.info(
                    "源 %s 命中，置信度 %d，低于阈值，继续查询源 %s",
                    provider.name,
                    score,
                    nxt,
                )
            else:
                logger.info("源 %s 命中，置信度 %d，低于阈值", provider.name, score)

        if not collected:
            logger.warning(f"所有 Provider 均无结果: {parsed.original_name}")
            return None

        best = choose_primary(collected, parsed)
        logger.info("源 %s 命中，置信度 %d，采用该结果", best.provider, best.confidence)
        others = [item for item in collected if item is not best]
        return await self._finalize(parsed, best, others)

    def _can_finish(self, collected: List[ProviderResult], parsed: ParsedFilename, threshold: int) -> bool:
        """番号相符的主源已经够格，并且标题是中日文，或已经有可替换的中日文标题。"""
        primary = choose_primary(collected, parsed)
        if primary.provider == "mock":
            return False
        if not matches_file_code(primary.metadata, parsed):
            return False
        if not meets_threshold(primary.confidence, primary.title_quality, threshold):
            return False
        if has_cjk(primary.metadata.title or ""):
            return True
        others = [item for item in collected if item is not primary and item.provider != "mock"]
        return cjk_title_candidate(primary.metadata, others, parsed) is not None

    async def _query_provider(self, provider: BaseProvider, parsed: ParsedFilename) -> ProviderResult:
        """查询一个源。异常和空结果都变成 ProviderResult，不向外抛。"""
        try:
            metadata = await provider.scrape(parsed)
        except Exception as exc:
            reason = classify_failure(exc)
            if reason == "无结果":
                logger.warning("源 %s 查询异常，按无结果继续: %s", provider.name, exc)
            else:
                logger.debug("源 %s 查询失败: %s", provider.name, exc, exc_info=True)
            return ProviderResult(provider=provider.name, status="error", reason=reason)

        if metadata is None or is_login_placeholder(metadata.title or ""):
            if metadata and is_login_placeholder(metadata.title or ""):
                logger.info("[%s] 忽略登录页标题: %s", provider.name, metadata.title)
            return ProviderResult(provider=provider.name, status="empty", reason="空结果")

        metadata = copy.deepcopy(metadata)
        metadata.poster_url = usable_poster_url(metadata.poster_url or "") or None
        if not metadata.source_provider:
            metadata.source_provider = provider.name
            metadata.scraper_source = provider.name
        if not _has_payload(metadata):
            return ProviderResult(provider=provider.name, status="empty", reason="空结果")
        return ProviderResult(provider=provider.name, metadata=metadata, status="hit")

    async def _finalize(
        self,
        parsed: ParsedFilename,
        chosen: ProviderResult,
        donors: List[ProviderResult],
    ) -> Metadata:
        """锁定主源，再按优先级用同一作品的其它结果补空字段。"""
        metadata = copy.deepcopy(chosen.metadata)
        if metadata is None:
            raise RuntimeError("主源缺少元数据")
        metadata.source_provider = chosen.provider
        metadata.scraper_source = chosen.provider
        if metadata.extra is None:
            metadata.extra = {}
        metadata.extra["confidence"] = chosen.confidence
        filled = fill_empty_fields(metadata, donors, parsed)
        for field_name, source in filled.items():
            logger.info("字段 %s 为空，使用源 %s 补空", field_name, source)
        self._prefer_cjk_title(parsed, metadata, donors)
        await self._extend_standard_title(parsed, metadata)
        return metadata

    def _log_miss(self, name: str, reason: str, nxt: Optional[str]) -> None:
        if nxt:
            logger.info("源 %s 返回 %s，继续查询源 %s", name, reason, nxt)
        else:
            logger.info("源 %s 返回 %s", name, reason)

    def _prefer_cjk_title(
        self,
        parsed: ParsedFilename,
        metadata: Metadata,
        donors: List[ProviderResult],
    ) -> None:
        """英文标题只换标题本身。番号、封面、演员和年份保持主源。"""
        picked = cjk_title_candidate(metadata, donors, parsed)
        if picked is None:
            return
        title, source = picked
        if title == (metadata.title or "").strip():
            return
        logger.info("标题为英文，使用源 %s 的中日文标题", source)
        metadata.title = title
        if metadata.extra is None:
            metadata.extra = {}
        metadata.extra["source_provider_title"] = source

    async def _extend_standard_title(self, parsed: ParsedFilename, metadata: Metadata) -> None:
        """标题为空、只剩番号，或仍是英文时，才向 R18.dev 要中日文标题。"""
        code = (parsed.code or metadata.code or "").strip()
        if not is_standard_code(code) or self._title_source is None:
            return
        if self._config.get("metadata", {}).get("complete_title", True) is False:
            return
        if metadata.source_provider == "r18dev":
            return
        current = (metadata.title or "").strip()
        needs_any_title = _title_needs_fill(current, code)
        needs_cjk = not has_cjk(current)
        if not needs_any_title and not needs_cjk:
            return
        try:
            extra = await self._title_source.lookup(parsed, code)
        except Exception as exc:
            logger.info("完整标题查询失败: %s", exc)
            return
        if extra is None:
            return
        chosen = (extra.title or "").strip()
        if not chosen or chosen == current or _title_needs_fill(chosen, code):
            return
        if needs_cjk and not needs_any_title:
            if not has_cjk(chosen) or title_quality(extra, parsed) < COMPLETE_TITLE:
                return
        logger.info("标题为英文或为空，使用源 r18dev 补标题: %s", chosen)
        metadata.title = chosen
        if metadata.extra is None:
            metadata.extra = {}
        metadata.extra["source_provider_title"] = "r18dev"

    def get_provider_info(self) -> List[Dict]:
        """获取所有已加载 Provider 的信息"""
        return [
            {
                "name": p.name,
                "display_name": p.display_name,
                "supported_types": p.supported_types,
            }
            for p in self._providers
        ]


def _title_needs_fill(title: str, code: str) -> bool:
    """空标题，或标题只是番号本身，才算标题缺失。"""
    text = (title or "").strip()
    if not text:
        return True
    folded = "".join(ch for ch in text.casefold() if ch.isalnum())
    code_folded = "".join(ch for ch in (code or "").casefold() if ch.isalnum())
    return bool(code_folded) and folded == code_folded


def _has_payload(metadata: Metadata) -> bool:
    values = (
        metadata.title,
        metadata.original_title,
        metadata.year,
        metadata.code,
        metadata.actors,
        metadata.genres,
        metadata.overview,
        metadata.summary,
        metadata.poster_url,
        metadata.studio,
    )
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, tuple)) and not value:
            continue
        return True
    return False
