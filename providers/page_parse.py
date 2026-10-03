"""从 HTML 里取出标题、封面和字段，供多个刮削源共用。"""

from __future__ import annotations

import re
from html import unescape
from typing import Optional
from urllib.parse import urljoin

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


def strip_tags(fragment: str) -> str:
    text = _TAG_RE.sub(" ", fragment or "")
    return _SPACE_RE.sub(" ", unescape(text)).strip()


def meta_content(html: str, prop: str) -> str:
    patterns = (
        rf'<meta[^>]+property=["\']{re.escape(prop)}["\'][^>]+content=["\']([^"\']*)["\']',
        rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+property=["\']{re.escape(prop)}["\']',
        rf'<meta[^>]+name=["\']{re.escape(prop)}["\'][^>]+content=["\']([^"\']*)["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, html or "", re.I)
        if match:
            return unescape(match.group(1)).strip()
    return ""


def tag_text(html: str, tag: str) -> str:
    match = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", html or "", re.I | re.S)
    return strip_tags(match.group(1)) if match else ""


def year_of(text: str) -> Optional[int]:
    match = re.search(r"(19|20)\d{2}", text or "")
    return int(match.group(0)) if match else None


_PLACEHOLDER_RE = re.compile(
    r"no[-_]?image|now[-_]?printing|no[-_]?photo|"
    r"(?:^|[/_-])logo(?:[/_.-]|\.(?:png|jpe?g|gif|webp))",
    re.I,
)


def absolute_url(url: str, base: str = "") -> str:
    """把站点内的相对封面地址补成可下载的绝对地址。"""
    text = (url or "").strip()
    if not text:
        return ""
    if text.startswith("//"):
        return "https:" + text
    if text.startswith(("http://", "https://")):
        return text
    root = (base or "").strip()
    if root.startswith("//"):
        root = "https:" + root
    if root and text.startswith("/"):
        return urljoin(root, text)
    return text


def is_placeholder_poster(url: str) -> bool:
    """站点默认图、无图占位和 logo 不能当封面。"""
    text = (url or "").strip()
    if not text:
        return True
    return _PLACEHOLDER_RE.search(text) is not None


def _is_fc2_thumb(url: str) -> bool:
    host = (url or "").lower()
    return "contents-thumbnail2.fc2.com" in host or "contents-thumbnail.fc2.com" in host


def _fc2_width(url: str, width: str) -> str:
    return re.sub(r"/w\d+/", f"/w{width}/", url, count=1, flags=re.I)


def upgrade_poster_url(url: str) -> str:
    """把已知的小图地址换成更大的一张。协议相对地址补成 https。"""
    text = (url or "").strip()
    if text.startswith("//"):
        text = "https:" + text
    if not text:
        return ""
    text = re.sub(r"ps\.jpg(?=($|\?))", "pl.jpg", text, flags=re.I)
    if _is_fc2_thumb(text):
        text = _fc2_width(text, "1280")
    return text


def poster_fallbacks(url: str) -> list[str]:
    """大图在前。大图失败时再试原来的小图。"""
    original = (url or "").strip()
    if original.startswith("//"):
        original = "https:" + original
    if not original or is_placeholder_poster(original):
        return []
    upgraded = upgrade_poster_url(original)
    ordered: list[str] = []

    def add(item: str) -> None:
        if item and item not in ordered and not is_placeholder_poster(item):
            ordered.append(item)

    add(upgraded)
    add(original)
    if re.search(r"pl\.jpg($|\?)", upgraded, re.I):
        add(re.sub(r"pl\.jpg", "ps.jpg", upgraded, count=1, flags=re.I))
    if _is_fc2_thumb(upgraded):
        add(_fc2_width(upgraded, "960"))
    return ordered


def usable_poster_url(url: str) -> str:
    """保存用的封面地址。占位图返回空字符串。"""
    candidates = poster_fallbacks(url)
    return candidates[0] if candidates else ""


def first_image(html: str, *needles: str) -> str:
    urls = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html or "", re.I)
    for needle in needles:
        for url in urls:
            if needle in url:
                return url
    for url in urls:
        if re.search(r"\.(?:jpg|jpeg|webp|png)(?:\?|$)", url, re.I):
            return url
    return ""


def bold_field(html: str, label: str) -> str:
    match = re.search(
        rf"<b>\s*{re.escape(label)}\s*</b>\s*:?\s*(.*?)(?:<br\s*/?>|</div>|</p>)",
        html or "",
        re.I | re.S,
    )
    return match.group(1) if match else ""


def anchor_names(fragment: str) -> list[str]:
    names = []
    for name in re.findall(r"<a\b[^>]*>([^<]+)</a>", fragment or "", re.I):
        cleaned = strip_tags(name)
        if cleaned and cleaned not in names:
            names.append(cleaned)
    return names
