"""R18.dev JSON 元数据。番号会转成 dvd_id，例如 SSIS-001 -> ssis00001。"""

from __future__ import annotations

import json
import re
from typing import Optional

from core.models import Actor, Metadata, ParsedFilename
from providers.page_parse import year_of
from providers.registry import register_provider
from providers.simple import DirectProvider

_API = "https://r18.dev/videos/vod/movies/detail/-/dvd_id={dvd_id}/json"
_CODE_RE = re.compile(r"^([A-Za-z]+)-?(\d+)$")
_STANDARD_CODE_RE = re.compile(r"^[A-Za-z]{2,12}-\d{2,6}$")
_SPACE_RE = re.compile(r"\s+")


def is_standard_code(code: str) -> bool:
    """普通品番，例如 RCTD-617。FC2-PPV-编号不符合。"""
    return _STANDARD_CODE_RE.match((code or "").strip()) is not None


def fuller_title(current: str, candidate: str) -> str:
    """候选标题更长、且包含当前标题时才采用。否则保持原标题。"""
    current_text = (current or "").strip()
    candidate_text = (candidate or "").strip()
    if not current_text or not candidate_text:
        return current_text
    if len(candidate_text) <= len(current_text):
        return current_text
    current_key = _SPACE_RE.sub("", current_text).casefold()
    candidate_key = _SPACE_RE.sub("", candidate_text).casefold()
    if current_key and current_key in candidate_key:
        return candidate_text
    return current_text


def dvd_id_of(code: str) -> str:
    match = _CODE_RE.match((code or "").strip())
    if not match:
        return re.sub(r"[^a-z0-9]", "", (code or "").lower())
    return f"{match.group(1).lower()}{int(match.group(2)):05d}"


def parse_r18(payload: dict, page_url: str = "") -> Optional[Metadata]:
    if not isinstance(payload, dict) or not payload.get("title"):
        return None
    actresses = []
    for item in payload.get("actresses") or []:
        if isinstance(item, dict) and item.get("name"):
            actresses.append(Actor(name=str(item["name"])))
    genres = []
    for item in payload.get("categories") or []:
        if isinstance(item, dict) and item.get("name"):
            genres.append(str(item["name"]))
    images = payload.get("images") or {}
    jacket = images.get("jacket_image") if isinstance(images, dict) else None
    poster = ""
    if isinstance(jacket, dict):
        poster = jacket.get("large2") or jacket.get("large") or ""
    maker = payload.get("maker") or {}
    label = payload.get("label") or {}
    studio = ""
    if isinstance(maker, dict):
        studio = maker.get("name") or ""
    elif isinstance(label, dict):
        studio = label.get("name") or ""
    released = str(payload.get("release_date") or "")
    runtime = payload.get("runtime_minutes")
    return Metadata(
        title=str(payload.get("title") or ""),
        code=(payload.get("dvd_id") or payload.get("content_id") or ""),
        year=year_of(released),
        actors=actresses,
        genres=genres,
        runtime=int(runtime) if isinstance(runtime, int) else None,
        studio=studio or None,
        poster_url=poster or None,
        source_url=page_url or payload.get("detail_url") or "",
        source_provider="r18dev",
    )


@register_provider("r18dev")
class R18DevProvider(DirectProvider):
    @property
    def name(self) -> str:
        return "r18dev"

    @property
    def display_name(self) -> str:
        return "R18.dev"

    def can_handle(self, parsed: ParsedFilename) -> bool:
        return bool(parsed.code) and super().can_handle(parsed)

    async def lookup(self, parsed: ParsedFilename, query: str) -> Optional[Metadata]:
        from providers.net import fetch_text

        code = parsed.code or query
        url = _API.format(dvd_id=dvd_id_of(code))
        text, final = await fetch_text(self, url, headers={"Accept": "application/json"})
        if not text:
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
        metadata = parse_r18(payload, final)
        if metadata and parsed.code and not metadata.code:
            metadata.code = parsed.code
        return metadata
