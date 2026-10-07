"""候选元数据的置信度，以及主源锁定后的空字段补全。

一部影片只选一个主源。其它源不能改写主源已有字段，
只能在番号一致（或双方都没有番号且标题完全相同）时填补空白，
并写入 source_provider_<字段名>。
"""

from __future__ import annotations

import copy
import logging
import re
from typing import Optional

from core.chinese_utils import has_cjk
from core.models import Metadata, ParsedFilename, ProviderResult
from providers.page_parse import usable_poster_url

logger = logging.getLogger(__name__)

# 标题本身达到这个分数，才允许因为总分达标而提前停止。
COMPLETE_TITLE = 40

_SPACE_RE = re.compile(r"\s+")
_CODE_RE = re.compile(r"[^A-Za-z0-9]")
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_SUBTITLE_RE = re.compile(r"[：:~～—]")
# 文件名可能是 FC2-123，官方源写 FC2-PPV-123，归一成同一种再比。
_FC2_CODE_RE = re.compile(r"^FC2(?:PPV)?(\d{5,8})$", re.I)

_SCALAR_FIELDS = (
    "title",
    "original_title",
    "year",
    "code",
    "runtime",
    "rating",
    "studio",
    "poster_url",
    "fanart_url",
)


def classify_failure(exc: BaseException) -> str:
    """把异常收成日志里使用的失败原因。"""
    text = f"{type(exc).__name__} {exc}".lower()
    if "timeout" in text or "timed out" in text:
        return "超时"
    if "404" in text or "not found" in text or "notfound" in text:
        return "404"
    return "无结果"


def score_metadata(meta: Metadata, parsed: ParsedFilename) -> tuple[int, int]:
    """返回 (置信度 0-100, 标题完整度 0-40)。"""
    quality = title_quality(meta, parsed)
    score = quality
    if meta.year:
        score += 15
    if usable_poster_url(meta.poster_url or ""):
        score += 20
    if meta.actors or meta.genres:
        score += 15
    overview = (meta.overview or meta.summary or "").strip()
    if overview or (isinstance(meta.runtime, int) and meta.runtime > 0):
        score += 10
    return min(score, 100), quality


def meets_threshold(score: int, title_quality_score: int, threshold: int) -> bool:
    """达到阈值且标题完整时才提前采用。阈值为 0 时，任意命中都停止。"""
    if threshold <= 0:
        return True
    return title_quality_score >= COMPLETE_TITLE and score >= threshold


def title_quality(meta: Metadata, parsed: ParsedFilename) -> int:
    """标题完整度：空或仅番号为 0，明显缺后缀为 10，过短为 20，完整为 40。"""
    title = (meta.title or "").strip()
    if not title:
        return 0
    code = (meta.code or parsed.code or "").strip()
    if code and _norm_code(title) == _norm_code(code):
        return 0

    hint = (parsed.title or "").strip()
    if hint and _norm_code(hint) != _norm_code(code or hint):
        compact_title = _compact(title)
        compact_hint = _compact(hint)
        if (
            compact_title
            and compact_hint
            and compact_hint.startswith(compact_title)
            and len(compact_hint) > len(compact_title)
        ):
            return 10

    if _is_complete_title(title):
        return 40
    return 20


def same_work(primary: Metadata, donor: Metadata, parsed: ParsedFilename) -> bool:
    """只有能确认是同一部作品时，才允许用次源补空。"""
    file_code = _norm_code(parsed.code or primary.code or "")
    primary_code = _norm_code(primary.code or "")
    donor_code = _norm_code(donor.code or "")
    if primary_code and donor_code and primary_code != donor_code:
        return False
    if file_code or donor_code:
        return bool(file_code and donor_code and file_code == donor_code)
    left = _compact(primary.title)
    right = _compact(donor.title)
    return bool(left and right and left == right)


def fill_empty_fields(
    primary: Metadata,
    donors: list[ProviderResult],
    parsed: ParsedFilename,
) -> dict[str, str]:
    """只填主源的空字段。返回 {字段名: 来源}。"""
    if primary.extra is None:
        primary.extra = {}
    filled: dict[str, str] = {}
    for donor_result in donors:
        donor = donor_result.metadata
        if donor is None:
            continue
        if not same_work(primary, donor, parsed):
            logger.debug("源 %s 与主源不是同一部作品，跳过补空", donor_result.provider)
            continue
        source = donor.source_provider or donor_result.provider
        for name in _SCALAR_FIELDS:
            _fill_scalar(primary, donor, name, source, filled)
        _fill_overview(primary, donor, source, filled)
        _fill_list(primary, donor, "actors", source, filled)
        _fill_list(primary, donor, "genres", source, filled)
    if primary.overview and not primary.summary:
        primary.summary = primary.overview
    elif primary.summary and not primary.overview:
        primary.overview = primary.summary
    return filled


def choose_primary(collected: list[ProviderResult], parsed: ParsedFilename) -> ProviderResult:
    """选出主源。mock 不压过真实源；已有番号相符的结果时，忽略番号不同的结果。"""
    usable = [item for item in collected if item.metadata is not None]
    real = [item for item in usable if item.provider != "mock"]
    pool = real or usable
    file_code = _norm_code(parsed.code or "")
    if file_code:
        matched = [
            item for item in pool
            if _norm_code(item.metadata.code or "") == file_code
        ]
        if matched:
            pool = matched
    return max(pool, key=lambda item: item.confidence)


def matches_file_code(meta: Metadata, parsed: ParsedFilename) -> bool:
    """有番号时必须和文件番号一致。文件或结果没有番号时不据此否决。"""
    file_code = _norm_code(parsed.code or "")
    meta_code = _norm_code(meta.code or "")
    if not file_code or not meta_code:
        return True
    return file_code == meta_code


def cjk_title_candidate(
    primary: Metadata,
    donors: list[ProviderResult],
    parsed: ParsedFilename,
) -> Optional[tuple[str, str]]:
    """在同一作品的其它结果里选一条完整中日文标题。

    返回 (标题, 来源)。主源标题已经含中日文、标题过短、番号不一致，
    或来源是 mock 时，不替换。
    """
    if has_cjk(primary.title or ""):
        return None
    chosen_title = ""
    chosen_source = ""
    chosen_score = -1
    for donor_result in donors:
        if donor_result.provider == "mock":
            continue
        donor = donor_result.metadata
        if donor is None or not same_work(primary, donor, parsed):
            continue
        title = (donor.title or "").strip()
        if not has_cjk(title) or title_quality(donor, parsed) < COMPLETE_TITLE:
            continue
        score = donor_result.confidence
        if score > chosen_score:
            chosen_score = score
            chosen_title = title
            chosen_source = donor.source_provider or donor_result.provider
    if not chosen_title:
        return None
    return chosen_title, chosen_source


def field_sources(meta: Metadata) -> dict[str, str]:
    """读出补空来源，键名形如 source_provider_poster_url。"""
    extra = meta.extra or {}
    found: dict[str, str] = {}
    for key, value in extra.items():
        if key.startswith("source_provider_") and isinstance(value, str) and value.strip():
            found[key] = value.strip()
    return found


def _fill_scalar(
    primary: Metadata,
    donor: Metadata,
    name: str,
    source: str,
    filled: dict[str, str],
) -> None:
    if not _is_blank(getattr(primary, name)):
        return
    value = getattr(donor, name)
    if _is_blank(value):
        return
    setattr(primary, name, copy.deepcopy(value))
    primary.extra[f"source_provider_{name}"] = source
    filled[name] = source
    if name == "poster_url" and donor.source_url:
        primary.extra["poster_referer"] = donor.source_url


def _fill_overview(
    primary: Metadata,
    donor: Metadata,
    source: str,
    filled: dict[str, str],
) -> None:
    if not (_is_blank(primary.overview) and _is_blank(primary.summary)):
        return
    text = (donor.overview or donor.summary or "").strip()
    if not text:
        return
    primary.overview = text
    primary.summary = text
    primary.extra["source_provider_overview"] = source
    filled["overview"] = source


def _fill_list(
    primary: Metadata,
    donor: Metadata,
    name: str,
    source: str,
    filled: dict[str, str],
) -> None:
    if getattr(primary, name):
        return
    value = getattr(donor, name)
    if not value:
        return
    setattr(primary, name, copy.deepcopy(value))
    primary.extra[f"source_provider_{name}"] = source
    filled[name] = source


def _is_blank(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value == 0
    return False


def _is_complete_title(title: str) -> bool:
    if len(title) >= 8:
        return True
    if len(title.split()) >= 3:
        return True
    if len(title) >= 6 and (_YEAR_RE.search(title) or _SUBTITLE_RE.search(title)):
        return True
    return False


def _compact(text: str) -> str:
    return _SPACE_RE.sub("", text or "").casefold()


def _norm_code(code: Optional[str]) -> str:
    text = _CODE_RE.sub("", code or "").upper()
    fc2 = _FC2_CODE_RE.fullmatch(text)
    if fc2:
        return f"FC2PPV{fc2.group(1)}"
    return text
