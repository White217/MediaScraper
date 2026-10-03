"""
Provider 模块测试
"""

import asyncio
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.models import ParsedFilename, VideoType, Metadata, Actor
from providers.base import BaseProvider
from providers.registry import (
    register_provider,
    create_provider,
    get_all_providers,
    list_provider_names,
    clear_registry,
)
from providers.aggregator import ProviderAggregator
from providers.mock import MockProvider


# ── BaseProvider 测试 ──


class TestBaseProvider:
    """测试 BaseProvider 基类"""

    def test_concrete_provider_name(self):
        mock = MockProvider()
        assert mock.name == "mock"

    def test_concrete_provider_display_name(self):
        mock = MockProvider()
        assert "Mock" in mock.display_name

    def test_supported_types_default(self):
        mock = MockProvider()
        types = mock.supported_types
        assert "movie" in types
        assert "episode" in types
        assert "coded" in types

    def test_can_handle_movie(self):
        mock = MockProvider()
        parsed = ParsedFilename(
            original_path="/test/movie.mkv",
            video_type=VideoType.MOVIE,
            title="Test",
            year=2020,
        )
        assert mock.can_handle(parsed) is True

    def test_can_handle_coded(self):
        mock = MockProvider()
        parsed = ParsedFilename(
            original_path="/test/code.mp4",
            video_type=VideoType.CODED,
            code="IPZZ-902",
        )
        assert mock.can_handle(parsed) is True

    def test_build_query_with_code(self):
        mock = MockProvider()
        parsed = ParsedFilename(
            original_path="/test/x.mp4",
            video_type=VideoType.CODED,
            code="IPZZ-902",
        )
        query = mock._build_query(parsed)
        assert query == "IPZZ-902"

    def test_build_query_with_title_year(self):
        mock = MockProvider()
        parsed = ParsedFilename(
            original_path="/test/x.mkv",
            video_type=VideoType.MOVIE,
            title="Inception",
            year=2010,
        )
        query = mock._build_query(parsed)
        assert "Inception" in query
        assert "2010" in query


# ── MockProvider 测试 ──


class TestMockProvider:
    """测试 MockProvider"""

    @pytest.mark.asyncio
    async def test_search_coded(self):
        mock = MockProvider()
        parsed = ParsedFilename(
            original_path="/test/IPZZ-902.mp4",
            video_type=VideoType.CODED,
            code="IPZZ-902",
        )
        results = await mock.search("IPZZ-902", parsed)
        assert len(results) > 0
        assert results[0].title == "IPZZ-902"

    @pytest.mark.asyncio
    async def test_search_movie(self):
        mock = MockProvider()
        parsed = ParsedFilename(
            original_path="/test/Inception.2010.mkv",
            video_type=VideoType.MOVIE,
            title="Inception",
            year=2010,
        )
        results = await mock.search("Inception 2010", parsed)
        assert len(results) > 0
        assert "Inception" in results[0].title

    @pytest.mark.asyncio
    async def test_search_fallback(self):
        mock = MockProvider()
        parsed = ParsedFilename(
            original_path="/test/unknown.mkv",
            video_type=VideoType.UNKNOWN,
            title="SomeRandomTitle",
        )
        results = await mock.search("SomeRandomTitle", parsed)
        assert len(results) > 0  # fallback should always return something

    @pytest.mark.asyncio
    async def test_get_detail_movie(self):
        mock = MockProvider()
        from core.models import SearchResult
        sr = SearchResult(
            provider="mock",
            title="Inception",
            year=2010,
            url="https://mock.example.com/movie/Inception",
            score=1.0,
        )
        meta = await mock.get_detail(sr)
        assert meta is not None
        assert meta.title == "Inception"
        assert meta.year == 2010
        assert meta.rating == 8.8
        assert len(meta.actors) >= 2

    @pytest.mark.asyncio
    async def test_get_detail_fallback(self):
        mock = MockProvider()
        from core.models import SearchResult
        sr = SearchResult(
            provider="mock",
            title="UnknownMovie",
            year=2024,
            url="https://mock.example.com/fallback",
            score=0.5,
        )
        meta = await mock.get_detail(sr)
        assert meta is not None
        assert meta.title == "UnknownMovie"
        assert meta.studio == "Mock Studio"

    @pytest.mark.asyncio
    async def test_scrape_full_pipeline(self):
        mock = MockProvider()
        parsed = ParsedFilename(
            original_path="/test/Inception.2010.1080p.mkv",
            video_type=VideoType.MOVIE,
            title="Inception",
            year=2010,
            resolution="1080p",
        )
        meta = await mock.scrape(parsed)
        assert meta is not None
        assert meta.title == "Inception"
        assert meta.source_provider == "mock"
        assert meta.source_url is not None


# ── Registry 测试 ──


class TestRegistry:
    """测试 Provider 注册表"""

    def test_mock_provider_registered(self):
        """MockProvider 应已通过 import 自动注册"""
        names = list_provider_names()
        assert "mock" in names

    def test_create_provider_mock(self):
        provider = create_provider("mock")
        assert provider is not None
        assert provider.name == "mock"

    def test_create_provider_unknown(self):
        provider = create_provider("nonexistent_provider")
        assert provider is None

    def test_get_all_providers(self):
        all_p = get_all_providers()
        assert "mock" in all_p

    def test_register_custom_provider(self):
        """测试动态注册新 Provider"""
        @register_provider
        class TestProvider(BaseProvider):
            @property
            def name(self):
                return "test_custom"

            @property
            def display_name(self):
                return "Test Custom Provider"

            async def search(self, query, parsed):
                return []

            async def get_detail(self, result):
                return None

        assert "test_custom" in list_provider_names()
        provider = create_provider("test_custom")
        assert provider is not None
        assert provider.display_name == "Test Custom Provider"


# ── Aggregator 测试 ──


class TestAggregator:
    """测试 ProviderAggregator"""

    def test_init_with_mock(self):
        config = {
            "providers": [
                {"name": "mock", "enabled": True},
            ],
        }
        agg = ProviderAggregator(config)
        assert len(agg.providers) == 1
        assert agg.providers[0].name == "mock"

    def test_init_skips_disabled(self):
        config = {
            "providers": [
                {"name": "mock", "enabled": False},
            ],
        }
        agg = ProviderAggregator(config)
        assert len(agg.providers) == 0

    def test_init_skips_unknown(self):
        config = {
            "providers": [
                {"name": "nonexistent", "enabled": True},
            ],
        }
        agg = ProviderAggregator(config)
        assert len(agg.providers) == 0

    @pytest.mark.asyncio
    async def test_scrape_returns_metadata(self):
        config = {
            "providers": [
                {"name": "mock", "enabled": True},
            ],
        }
        agg = ProviderAggregator(config)
        parsed = ParsedFilename(
            original_path="/test/Inception.2010.mkv",
            video_type=VideoType.MOVIE,
            title="Inception",
            year=2010,
        )
        meta = await agg.scrape(parsed)
        assert meta is not None
        assert meta.title == "Inception"

    @pytest.mark.asyncio
    async def test_scrape_priority_order(self):
        """测试 Provider 优先级：第一个有效结果胜出"""
        config = {
            "providers": [
                {"name": "mock", "enabled": True},
            ],
        }
        agg = ProviderAggregator(config)

        async def no_extra_title(parsed, query):
            return None

        agg._title_source.lookup = no_extra_title
        parsed = ParsedFilename(
            original_path="/test/IPZZ-902.mp4",
            video_type=VideoType.CODED,
            code="IPZZ-902",
        )
        meta = await agg.scrape(parsed)
        assert meta is not None
        assert meta.code == "IPZZ-902"
        assert meta.source_provider == "mock"

    def test_get_provider_info(self):
        config = {
            "providers": [
                {"name": "mock", "enabled": True},
            ],
        }
        agg = ProviderAggregator(config)
        info = agg.get_provider_info()
        assert len(info) == 1
        assert info[0]["name"] == "mock"
        assert "movie" in info[0]["supported_types"]

    @pytest.mark.asyncio
    async def test_existing_title_is_not_replaced_by_r18(self):
        config = {"providers": [{"name": "mock", "enabled": True}]}
        agg = ProviderAggregator(config)
        full = (
            "ROCKET16周年記念ユーザーリクエスト祭り 無様エロ洗脳アプリ "
            "〜無様でエロい格好になる不幸が降りかかり洗脳が進むと羞恥芸を全力でやってしまう女に改変〜"
        )
        called = []

        async def longer_title(parsed, query):
            called.append(query)
            return Metadata(
                title=full,
                poster_url="https://example.com/other.jpg",
                source_provider="r18dev",
            )

        agg._title_source.lookup = longer_title
        parsed = ParsedFilename(
            original_path="/test/RCTD-617.mp4",
            video_type=VideoType.CODED,
            code="RCTD-617",
            title="無様エロ洗脳アプリ",
        )
        meta = await agg.scrape(parsed)
        assert called == []
        assert meta.title == "無様エロ洗脳アプリ"
        assert meta.source_provider == "mock"
        assert meta.poster_url != "https://example.com/other.jpg"

    @pytest.mark.asyncio
    async def test_code_only_title_can_be_filled_from_r18(self):
        config = {"providers": [{"name": "mock", "enabled": True}]}
        agg = ProviderAggregator(config)
        full = "無様エロ洗脳アプリの完全なタイトル"

        async def longer_title(parsed, query):
            return Metadata(title=full, poster_url="https://example.com/other.jpg", source_provider="r18dev")

        agg._title_source.lookup = longer_title
        parsed = ParsedFilename(
            original_path="/test/IPZZ-902.mp4",
            video_type=VideoType.CODED,
            code="IPZZ-902",
        )
        meta = await agg.scrape(parsed)
        assert meta.title == full
        assert meta.source_provider == "mock"
        assert meta.extra["source_provider_title"] == "r18dev"
        assert meta.poster_url != "https://example.com/other.jpg"

    @pytest.mark.asyncio
    async def test_unrelated_r18_title_is_kept_out(self):
        config = {"providers": [{"name": "mock", "enabled": True}]}
        agg = ProviderAggregator(config)

        async def other_title(parsed, query):
            return Metadata(title="完全不同的另一部作品标题很长很长")

        agg._title_source.lookup = other_title
        parsed = ParsedFilename(
            original_path="/test/RCTD-617.mp4",
            video_type=VideoType.CODED,
            code="RCTD-617",
            title="無様エロ洗脳アプリ",
        )
        meta = await agg.scrape(parsed)
        assert meta.title == "無様エロ洗脳アプリ"

    @pytest.mark.asyncio
    async def test_fc2_code_does_not_query_r18(self):
        config = {"providers": [{"name": "mock", "enabled": True}]}
        agg = ProviderAggregator(config)
        called = []

        async def should_not_run(parsed, query):
            called.append(query)
            return Metadata(title="不应使用")

        agg._title_source.lookup = should_not_run
        parsed = ParsedFilename(
            original_path="/test/FC2-PPV-1085593.mp4",
            video_type=VideoType.CODED,
            code="FC2-PPV-1085593",
            title="FC2 标题",
        )
        meta = await agg.scrape(parsed)
        assert called == []
        assert meta.title == "FC2 标题"
