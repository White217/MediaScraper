"""把同一部影片的多个视频文件识别成分段。

只有同一批里至少两个文件、番号相同、末尾标记能排成从 1 开始的连续序号时，
才写入 part_index。单独一个文件、复制后缀 (1)、以及粘在番号上的数字都不会改名。
"""

from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from core.models import ParsedFilename

_NOISE_SUFFIX = re.compile(
    r"(?:[\s._-]+(?:2160p|1080p|720p|480p|4k|uhd|x264|x265|h264|h265|hevc|"
    r"bluray|blu-ray|web-?dl|webrip|hdrip))+$",
    re.IGNORECASE,
)

_CANON_RE = re.compile(
    r"(?P<suffix>[\s._-]*[（(]\s*Part\s*(?P<num>\d+)\s*[）)])\s*$",
    re.IGNORECASE,
)
_CN_RE = re.compile(
    r"(?P<suffix>(?:[\s._-]*[（(]\s*(?P<wrapped>[上中下])(?:集|部|篇)?\s*[）)]|"
    r"[\s._-]+(?P<bare>[上中下])(?:集|部|篇)?))\s*$"
)
_NAMED_RE = re.compile(
    r"(?P<suffix>[\s._-]*(?:part|pt|cd|disc|disk)\s*0*(?P<num>\d{1,2}))\s*$",
    re.IGNORECASE,
)
_NUM_RE = re.compile(r"(?P<suffix>[\s._-]+(?P<num>\d{1,2}))\s*$")
_LETTER_RE = re.compile(r"(?P<suffix>[\s._-]+(?P<letter>[abcABC]))\s*$")
_GLUED_LETTER_RE = re.compile(r"([abcABC])$")

_CN_VALUE = {"上": 1, "中": 2, "下": 3}


@dataclass
class PartMark:
    kind: str
    value: int
    suffix: str


def part_label(index: int) -> str:
    return f"（Part {index}）"


def apply_part_label(stem: str, code: str, part_index: int) -> str:
    """把分段标记插到番号后面。已经带同样标记时不重复添加。"""
    label = part_label(part_index)
    if label in stem:
        return stem
    if code and stem.upper().startswith(code.upper()):
        head = stem[: len(code)]
        rest = stem[len(code) :].lstrip(" -_.")
        if rest:
            return f"{head}{label} {rest}"
        return f"{head}{label}"
    return f"{stem} {label}".strip()


def _peel_noise(stem: str) -> str:
    current = stem.strip()
    while True:
        peeled = _NOISE_SUFFIX.sub("", current).strip(" ._-")
        if peeled == current:
            return current
        current = peeled


def detect_part_mark(stem: str, code: str = "") -> PartMark | None:
    """识别文件名末尾的分段标记。识别不到时返回 None。"""
    text = _peel_noise(stem)
    if not text:
        return None

    canon = _CANON_RE.search(text)
    if canon:
        return PartMark("canon", int(canon.group("num")), canon.group("suffix"))

    cn = _CN_RE.search(text)
    if cn:
        token = cn.group("wrapped") or cn.group("bare")
        return PartMark("cn", _CN_VALUE[token], cn.group("suffix"))

    named = _NAMED_RE.search(text)
    if named:
        return PartMark("num", int(named.group("num")), named.group("suffix"))

    number = _NUM_RE.search(text)
    if number:
        return PartMark("num", int(number.group("num")), number.group("suffix"))

    letter = _LETTER_RE.search(text)
    if letter:
        return PartMark(
            "letter",
            ord(letter.group("letter").lower()) - ord("a") + 1,
            letter.group("suffix"),
        )

    return _glued_letter(text, code)


def _glued_letter(stem: str, code: str) -> PartMark | None:
    """只接受紧贴在番号后面的单个 a/b/c，避免把更长番号拆开。"""
    if not code:
        return None
    pos = stem.upper().rfind(code.upper())
    if pos < 0:
        return None
    after = stem[pos + len(code) :]
    if not _GLUED_LETTER_RE.fullmatch(after.strip()):
        return None
    letter = after.strip().lower()
    return PartMark("letter", ord(letter) - ord("a") + 1, after)


def _contiguous_from_one(values: list[int]) -> bool:
    unique = sorted(set(values))
    return unique == list(range(1, len(unique) + 1))


def _strip_suffix(title: str, suffix: str) -> str:
    if suffix and title.endswith(suffix):
        return title[: -len(suffix)].strip(" -_.")
    return title.strip(" -_.")


def _title_without_mark(title: str, mark: PartMark) -> str:
    """从标题里去掉分段标记。只剩标记本身时，标题视为空。"""
    cleaned = _strip_suffix(title, mark.suffix)
    cleaned = _strip_suffix(cleaned, mark.suffix.strip())
    token = mark.suffix.strip(" -_.()（）集部篇")
    comparable = cleaned.strip(" -_.()（）")
    if comparable in {token, str(mark.value)}:
        return ""
    return cleaned


def assign_part_indexes(items: list[ParsedFilename], enabled: bool = True) -> None:
    """就地写入分段序号。不成组的文件保持 part_index 为空。"""
    for item in items:
        item.part_index = None
        item.part_group = ""
    if not enabled:
        return

    groups: dict[str, list[tuple[ParsedFilename, PartMark]]] = defaultdict(list)
    for item in items:
        if not item.code:
            continue
        stem = os.path.splitext(item.original_name or os.path.basename(item.original_path))[0]
        mark = detect_part_mark(stem, item.code)
        if mark is None or mark.value < 1:
            continue
        groups[item.code.upper()].append((item, mark))

    for code, pairs in groups.items():
        _finalize_group(code, pairs)


def _finalize_group(code: str, pairs: list[tuple[ParsedFilename, PartMark]]) -> None:
    if len(pairs) < 2:
        return

    kinds = {mark.kind for _, mark in pairs}
    values = {mark.value for _, mark in pairs}
    if kinds == {"cn"} and values == {1, 3}:
        for _, mark in pairs:
            if mark.value == 3:
                mark.value = 2

    counts = Counter(mark.value for _, mark in pairs)
    kept = [(item, mark) for item, mark in pairs if counts[mark.value] == 1]
    if len(kept) < 2:
        return
    if not _contiguous_from_one([mark.value for _, mark in kept]):
        return

    for item, mark in kept:
        item.part_index = mark.value
        item.part_group = code
        if item.title:
            item.title = _title_without_mark(item.title, mark) or None
