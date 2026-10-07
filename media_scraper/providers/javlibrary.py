"""
JavLibrary Provider
从 javlibrary.com 刮削元数据（直连 HTML 刮削，best-effort）。

- 搜索: https://www.javlibrary.com/en/vl_searchbyid.php?keyword=<CODE>
- 详情: https://www.javlibrary.com/en/?v=<video_id>
- 数据: 番号、标题、演员、厂商、厂牌、类型、发行日期、时长、评分、封面

注意：
- javlibrary.com 启用了 Cloudflare 防护，直连可能返回 403；
  此时自动跳过，不影响其他 Provider。建议配置代理。
- 遵守站点 robots.txt 与 ToS。
"""

import logging
import re
from typing import List, Optional

from bs4 import BeautifulSoup

from core.models import (
    Actor,
    Metadata,
    ParsedFilename,
    SearchResult,
)
from providers.base import BaseProvider
from providers.page_parse import usable_poster_url
from providers.registry import register_provider

logger = logging.getLogger(__name__)

BASE_URL = "https://www.javlibrary.com"

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.javlibrary.com/",
}


@register_provider("javlibrary")
class JavLibraryProvider(BaseProvider):
    """javlibrary.com HTML Provider（best-effort）。"""

    def __init__(self, proxy: Optional[str] = None, **kwargs):
        super().__init__(proxy=proxy, connector=kwargs.get("connector"), **kwargs)
        self._proxy = proxy or None

    @property
    def name(self) -> str:
        return "javlibrary"

    @property
    def display_name(self) -> str:
        return "JavLibrary"

    @property
    def supported_types(self) -> List[str]:
        return ["coded"]

    async def _fetch_html(self, url: str) -> Optional[str]:
        from providers.net import fetch_text

        text, _final = await fetch_text(self, url, headers=_DEFAULT_HEADERS)
        return text

    async def search(self, query: str, parsed: ParsedFilename) -> List[SearchResult]:
        search_url = f"{BASE_URL}/en/vl_searchbyid.php?keyword={query}"
        html = await self._fetch_html(search_url)
        if not html:
            return []

        soup = BeautifulSoup(html, "lxml")

        # 情况1：唯一结果时站点直接跳转到详情页
        title_div = soup.select_one("#video_title, .post-title")
        if title_div:
            a = title_div.find("a", href=True)
            url = None
            title_text = title_div.get_text(strip=True)
            if a:
                href = a["href"]
                url = self._abs(href)
                title_text = a.get_text(strip=True)
            return [
                SearchResult(
                    provider=self.name,
                    title=title_text,
                    url=url or search_url,
                )
            ]

        # 情况2：搜索结果列表
        results = []
        for div in soup.select(".video, .previewvideo, .videothumblist .video"):
            a = div.find("a", href=True)
            if not a:
                continue
            t_div = div.select_one(".title, .video-title")
            title_text = (
                t_div.get_text(strip=True) if t_div else a.get_text(strip=True)
            )
            img = div.find("img")
            poster = img.get("src") if img else None
            if poster and poster.startswith("//"):
                poster = "https:" + poster
            results.append(
                SearchResult(
                    provider=self.name,
                    title=title_text,
                    url=self._abs(a["href"]),
                    poster_url=poster,
                )
            )
        return results

    def _abs(self, href: str) -> str:
        if href.startswith("http"):
            return href
        if href.startswith("/"):
            return BASE_URL + href
        return f"{BASE_URL}/en/{href}"

    async def get_detail(self, result: SearchResult) -> Optional[Metadata]:
        if not result.url:
            return None
        html = await self._fetch_html(result.url)
        if not html:
            return None
        return self._parse_detail(html, result.url)

    def _parse_detail(self, html: str, detail_url: str) -> Optional[Metadata]:
        soup = BeautifulSoup(html, "lxml")

        title_div = soup.select_one("#video_title, .post-title")
        title = ""
        if title_div:
            a = title_div.find("a")
            title = (
                a.get_text(strip=True) if a else title_div.get_text(strip=True)
            )
        if not title:
            h1 = soup.find("h1")
            title = h1.get_text(strip=True) if h1 else ""
        if not title:
            return None

        # 封面
        jacket = soup.select_one("#video_jacket_img")
        poster_url = None
        if jacket and jacket.get("src"):
            poster_url = usable_poster_url(jacket["src"]) or None

        # 信息表
        info = {}
        for td in soup.select("#video_info td.header, .post-info td.header"):
            key = td.get_text(strip=True).rstrip(":").strip().lower()
            sibling = td.find_next_sibling("td")
            if sibling:
                info[key] = sibling.get_text(" ", strip=True)

        code = info.get("code")
        year = None
        release = info.get("release date") or info.get("date")
        if release:
            ym = re.search(r"(19|20)\d{2}", release)
            if ym:
                year = int(ym.group(0))

        runtime = None
        length = info.get("length") or info.get("runtime")
        if length:
            mm = re.search(r"(\d+)", length)
            if mm:
                runtime = int(mm.group(1))

        rating = None
        rating_text = info.get("rating") or info.get("user rating")
        if rating_text:
            rm = re.search(r"([\d.]+)", rating_text)
            if rm:
                try:
                    rating = float(rm.group(1))
                except ValueError:
                    rating = None

        # 演员
        actors = []
        for span in soup.select("#video_info span.star a, .star a"):
            name = span.get_text(strip=True)
            if name:
                actors.append(Actor(name=name))

        # 类型
        genres = []
        for a in soup.select("#video_info span.genre a, .genre a"):
            g = a.get_text(strip=True)
            if g and g not in genres:
                genres.append(g)

        meta = Metadata(
            title=title,
            original_title=title,
            year=year,
            code=code,
            actors=actors,
            genres=genres,
            runtime=runtime,
            rating=rating,
            studio=info.get("maker"),
            poster_url=poster_url,
            source_url=detail_url,
            extra={"label": info.get("label")},
        )
        return meta
