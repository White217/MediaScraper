"""多源接续刮削：低置信度继续、失败继续、主源锁定后只补空字段。"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.models import Actor, Metadata, ParsedFilename, VideoType
from metadata.json_writer import metadata_to_dict
from metadata.nfo import build_movie_nfo
from providers.aggregator import ProviderAggregator
from providers.confidence import score_metadata, title_quality


class ScriptedProvider:
    def __init__(self, name, outcome):
        self.name = name
        self.display_name = name
        self.supported_types = ["movie", "coded", "episode"]
        self.outcome = outcome
        self.calls = 0

    def can_handle(self, parsed):
        return True

    async def scrape(self, parsed):
        self.calls += 1
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _agg(*providers, threshold=60):
    agg = ProviderAggregator(
        {
            "providers": [{"name": "mock", "enabled": True}],
            "metadata": {"confidence_threshold": threshold, "complete_title": True},
        }
    )
    agg._providers = list(providers)

    async def no_title(parsed, query):
        return None

    if agg._title_source is not None:
        agg._title_source.lookup = no_title
    return agg


def _parsed():
    return ParsedFilename(
        original_path="/test/RCTD-657.mp4",
        original_name="RCTD-657.mp4",
        video_type=VideoType.CODED,
        code="RCTD-657",
        title="蜘蛛侠2",
    )


def _short():
    return Metadata(
        title="蜘蛛侠",
        code="RCTD-657",
        actors=[Actor(name="演员甲")],
        poster_url="https://example.com/a.jpg",
        source_url="https://source-a.example/a",
        source_provider="A",
    )


def _full():
    return Metadata(
        title="完整的作品标题在这里",
        code="RCTD-657",
        year=2024,
        overview="主源简介",
        source_url="https://source-c.example/c",
        source_provider="C",
    )


class TestConfidence:
    def test_short_title_scores_below_complete_title(self):
        parsed = _parsed()
        quality = title_quality(_short(), parsed)
        score, stored_quality = score_metadata(_short(), parsed)
        assert quality == 10
        assert stored_quality == 10
        assert score < 60

    def test_code_only_title_is_not_complete(self):
        meta = Metadata(title="RCTD-657", code="RCTD-657", year=2024, poster_url="https://example.com/p.jpg")
        score, quality = score_metadata(meta, _parsed())
        assert quality == 0
        assert score < 60

    def test_complete_record_meets_default_threshold(self):
        meta = Metadata(
            title="完整的作品标题在这里",
            code="RCTD-657",
            year=2024,
            actors=[Actor(name="演员甲")],
            genres=["剧情"],
            overview="简介",
            poster_url="https://example.com/p.jpg",
        )
        score, quality = score_metadata(meta, _parsed())
        assert quality == 40
        assert score >= 60


class TestFallbackChain:
    @pytest.mark.asyncio
    async def test_low_score_then_404_continues_to_later_source(self, caplog):
        caplog.set_level("INFO")
        source_a = ScriptedProvider("A", _short())
        source_b = ScriptedProvider("B", RuntimeError("404 Not Found"))
        source_c = ScriptedProvider("C", _full())
        meta = await _agg(source_a, source_b, source_c).scrape(_parsed())

        assert source_a.calls == 1
        assert source_b.calls == 1
        assert source_c.calls == 1
        assert meta is not None
        assert meta.source_provider == "C"
        assert meta.title == "完整的作品标题在这里"
        assert meta.year == 2024
        assert meta.overview == "主源简介"
        assert meta.actors[0].name == "演员甲"
        assert meta.poster_url == "https://example.com/a.jpg"
        assert meta.extra["source_provider_actors"] == "A"
        assert meta.extra["source_provider_poster_url"] == "A"
        assert meta.extra["poster_referer"] == "https://source-a.example/a"
        assert "source_provider_title" not in meta.extra
        assert "源 A 命中，置信度" in caplog.text
        assert "低于阈值，继续查询源 B" in caplog.text
        assert "源 B 返回 404，继续查询源 C" in caplog.text
        assert "源 C 命中，置信度 65，采用该结果" in caplog.text

    @pytest.mark.asyncio
    async def test_empty_and_timeout_keep_searching(self, caplog):
        caplog.set_level("INFO")
        source_a = ScriptedProvider("A", TimeoutError("timed out"))
        source_b = ScriptedProvider("B", None)
        source_c = ScriptedProvider("C", _full())
        meta = await _agg(source_a, source_b, source_c).scrape(_parsed())

        assert meta is not None
        assert meta.source_provider == "C"
        assert meta.actors == []
        assert meta.poster_url is None
        assert "源 A 返回 超时，继续查询源 B" in caplog.text
        assert "源 B 返回 空结果，继续查询源 C" in caplog.text

    @pytest.mark.asyncio
    async def test_falls_back_to_earlier_source_when_later_ones_are_worse(self, caplog):
        caplog.set_level("INFO")
        source_a = ScriptedProvider("A", _short())
        source_b = ScriptedProvider("B", RuntimeError("404 Not Found"))
        source_c = ScriptedProvider("C", Metadata(title="另一条", code="RCTD-657", source_provider="C"))
        meta = await _agg(source_a, source_b, source_c).scrape(_parsed())

        assert meta is not None
        assert meta.source_provider == "A"
        assert meta.title == "蜘蛛侠"
        assert meta.poster_url == "https://example.com/a.jpg"
        assert "source_provider_title" not in meta.extra
        assert "源 A 命中，置信度" in caplog.text
        assert "采用该结果" in caplog.text

    @pytest.mark.asyncio
    async def test_high_confidence_stops_the_chain(self):
        source_a = ScriptedProvider(
            "A",
            Metadata(
                title="完整的作品标题在这里",
                code="RCTD-657",
                year=2024,
                actors=[Actor(name="演员甲")],
                genres=["剧情"],
                overview="简介",
                runtime=120,
                poster_url="https://example.com/a.jpg",
                source_provider="A",
            ),
        )
        source_b = ScriptedProvider("B", _full())
        meta = await _agg(source_a, source_b).scrape(_parsed())

        assert source_a.calls == 1
        assert source_b.calls == 0
        assert meta.source_provider == "A"
        assert meta.title == "完整的作品标题在这里"

    @pytest.mark.asyncio
    async def test_threshold_zero_keeps_first_hit(self):
        source_a = ScriptedProvider("A", _short())
        source_b = ScriptedProvider("B", _full())
        meta = await _agg(source_a, source_b, threshold=0).scrape(_parsed())

        assert source_b.calls == 0
        assert meta.source_provider == "A"

    @pytest.mark.asyncio
    async def test_different_code_does_not_fill_fields(self):
        source_a = ScriptedProvider(
            "A",
            Metadata(
                title="蜘蛛侠",
                code="ABP-123",
                actors=[Actor(name="不该混入")],
                poster_url="https://example.com/wrong.jpg",
                source_provider="A",
            ),
        )
        source_b = ScriptedProvider("B", _full())
        meta = await _agg(source_a, source_b).scrape(_parsed())

        assert meta.source_provider == "C" or meta.source_provider == "B"
        assert meta.title == "完整的作品标题在这里"
        assert meta.actors == []
        assert meta.poster_url is None
        assert "source_provider_actors" not in meta.extra
        assert "source_provider_poster_url" not in meta.extra

    @pytest.mark.asyncio
    async def test_nfo_and_json_record_primary_and_fill_sources(self):
        source_a = ScriptedProvider("A", _short())
        source_b = ScriptedProvider("B", None)
        source_c = ScriptedProvider("C", _full())
        meta = await _agg(source_a, source_b, source_c).scrape(_parsed())

        payload = metadata_to_dict(meta)
        assert payload["source_provider"] == "C"
        assert payload["source_provider_actors"] == "A"
        assert payload["source_provider_poster_url"] == "A"
        assert payload["title"] == "完整的作品标题在这里"
        assert "source_provider_title" not in payload

        nfo = build_movie_nfo(meta)
        assert "<source_provider>C</source_provider>" in nfo
        assert "<source_provider_actors>A</source_provider_actors>" in nfo
        assert "<source_provider_poster_url>A</source_provider_poster_url>" in nfo
        assert "<title>完整的作品标题在这里</title>" in nfo


def _english():
    return Metadata(
        title="Breaking Free from Abstinence",
        original_title="Breaking Free from Abstinence",
        code="RCTD-657",
        year=2024,
        actors=[Actor(name="Momona")],
        poster_url="https://example.com/en.jpg",
        studio="SOD",
        source_provider="javdatabase",
    )


def _japanese():
    return Metadata(
        title="禁欲解放 人生初の完全な日文标题",
        code="RCTD-657",
        year=2024,
        actors=[Actor(name="恋渕ももな")],
        poster_url="https://example.com/ja.jpg",
        source_provider="javlibrary",
    )


class TestEnglishTitleIsLowest:
    @pytest.mark.asyncio
    async def test_english_hit_keeps_searching_and_replaces_title_only(self, caplog):
        caplog.set_level("INFO")
        source_a = ScriptedProvider("javdatabase", _english())
        source_b = ScriptedProvider("javlibrary", _japanese())
        meta = await _agg(source_a, source_b).scrape(_parsed())

        assert source_b.calls == 1
        assert meta.source_provider == "javdatabase"
        assert meta.title == "禁欲解放 人生初の完全な日文标题"
        assert meta.original_title == "Breaking Free from Abstinence"
        assert meta.poster_url == "https://example.com/en.jpg"
        assert meta.actors[0].name == "Momona"
        assert meta.studio == "SOD"
        assert meta.extra["source_provider_title"] == "javlibrary"
        assert "标题为英文，继续查询源 javlibrary" in caplog.text
        assert "使用源 javlibrary 的中日文标题" in caplog.text

    @pytest.mark.asyncio
    async def test_short_or_other_code_or_mock_cannot_replace_english(self):
        short = Metadata(title="禁欲", code="RCTD-657", source_provider="javbus")
        other = Metadata(
            title="另一部作品的完整日文标题在这里",
            code="ABP-001",
            year=2024,
            poster_url="https://example.com/wrong.jpg",
            source_provider="javbus",
        )
        mock = Metadata(
            title="禁欲解放 来自文件名的日文标题",
            code="RCTD-657",
            year=2024,
            poster_url="https://example.com/mock.jpg",
            source_provider="mock",
        )
        meta = await _agg(
            ScriptedProvider("javdatabase", _english()),
            ScriptedProvider("javbus", short),
            ScriptedProvider("other", other),
            ScriptedProvider("mock", mock),
        ).scrape(_parsed())

        assert meta.title == "Breaking Free from Abstinence"
        assert meta.poster_url == "https://example.com/en.jpg"
        assert "source_provider_title" not in meta.extra

    @pytest.mark.asyncio
    async def test_cjk_title_still_stops_the_chain(self):
        source_a = ScriptedProvider("javdb", _japanese())
        source_b = ScriptedProvider("javlibrary", _english())
        meta = await _agg(source_a, source_b).scrape(_parsed())

        assert source_b.calls == 0
        assert meta.source_provider == "javlibrary" or meta.source_provider == "javdb"
        assert meta.title == "禁欲解放 人生初の完全な日文标题"
        assert meta.poster_url == "https://example.com/ja.jpg"

    @pytest.mark.asyncio
    async def test_r18_can_replace_english_title_only(self):
        source = ScriptedProvider("javdatabase", _english())
        agg = _agg(source)

        async def japanese(parsed, query):
            return Metadata(
                title="禁欲解放 来自补充源的完整日文标题",
                poster_url="https://example.com/r18.jpg",
                source_provider="r18dev",
            )

        agg._title_source.lookup = japanese
        meta = await agg.scrape(_parsed())

        assert meta.source_provider == "javdatabase"
        assert meta.title == "禁欲解放 来自补充源的完整日文标题"
        assert meta.poster_url == "https://example.com/en.jpg"
        assert meta.extra["source_provider_title"] == "r18dev"
