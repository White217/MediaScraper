"""FC2 官方目录。只处理文件名里带 FC2 和数字编号的条目。"""

from __future__ import annotations

import re
from typing import Optional

from core.models import Metadata, ParsedFilename
from providers.page_parse import meta_content, tag_text, usable_poster_url
from providers.registry import register_provider
from providers.simple import DirectProvider

_ID_RE = re.compile(r"FC2[-_ ]?(?:PPV[-_ ]?)?(\d{5,8})", re.I)
_BASE = "https://adult.contents.fc2.com/article/{article_id}/"
_LOGIN_TITLES = {
    "登入", "登录", "ログイン", "ログインする",
    "login", "log in", "signin", "sign in",
}


def article_url(article_id: str) -> str:
    return _BASE.format(article_id=article_id)


def is_login_wall(title: str, page_url: str = "") -> bool:
    """未登录时官方页会返回登录墙，标题经常就是「登入」。"""
    url = (page_url or "").lower()
    if any(part in url for part in (
        "/login", "secure.id.fc2.com", "id.fc2.com", "accounts.fc2.com",
    )):
        return True
    text = (title or "").strip().lower()
    if not text:
        return False
    compact = re.sub(r"\s+", "", text)
    if compact in _LOGIN_TITLES or text in _LOGIN_TITLES:
        return True
    return len(text) <= 20 and any(mark in text for mark in ("登入", "登录", "ログイン", "login"))


def fc2_article_id(parsed: ParsedFilename, query: str = "") -> str:
    blob = " ".join(part for part in (parsed.code, parsed.original_name, query) if part)
    match = _ID_RE.search(blob)
    return match.group(1) if match else ""


def _class_slice(html: str, class_name: str, limit: int) -> str:
    match = re.search(rf'class="[^"]*\b{class_name}\b[^"]*"', html or "", re.I)
    if not match:
        return ""
    return html[match.start(): match.start() + limit]


def fc2_poster_url(html: str) -> str:
    """主图优先，其次样品原图，最后才用 og:image。占位图丢掉。"""
    candidates: list[str] = []
    main = _class_slice(html, "items_article_MainitemThumb", 1500)
    if main:
        image = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', main, re.I)
        if image:
            candidates.append(image.group(1))
    sample = _class_slice(html, "items_article_SampleImages", 8000)
    if sample:
        for href in re.findall(r'<a[^>]+href=["\']([^"\']+)["\']', sample, re.I):
            if "storage.contents.fc2.com" in href:
                candidates.append(href)
    social = meta_content(html, "og:image")
    if social:
        candidates.append(social)
    for url in candidates:
        chosen = usable_poster_url(url)
        if chosen:
            return chosen
    return ""


def parse_fc2(html: str, article_id: str, page_url: str = "") -> Optional[Metadata]:
    if not html or "items_notfound" in html:
        return None
    title = meta_content(html, "og:title") or tag_text(html, "h2") or tag_text(html, "h1")
    if not title or "not found" in title.lower() or is_login_wall(title, page_url):
        return None
    return Metadata(
        title=title,
        code=f"FC2-PPV-{article_id}",
        poster_url=fc2_poster_url(html) or None,
        source_url=page_url,
        source_provider="fc2",
    )


@register_provider("fc2")
class Fc2Provider(DirectProvider):
    @property
    def name(self) -> str:
        return "fc2"

    @property
    def display_name(self) -> str:
        return "FC2"

    def can_handle(self, parsed: ParsedFilename) -> bool:
        return bool(fc2_article_id(parsed))

    async def lookup(self, parsed: ParsedFilename, query: str) -> Optional[Metadata]:
        from providers.net import fetch_text

        article_id = fc2_article_id(parsed, query)
        if not article_id:
            return None
        from core.fc2_auth import auth_headers, load_session

        url = article_url(article_id)
        # 不走页面缓存：未登录时的「登入」页不能被下次请求重复使用。
        text, final = await fetch_text(
            self, url, headers=auth_headers(load_session()), use_cache=False,
        )
        if not text:
            return None
        return parse_fc2(text, article_id, final)
