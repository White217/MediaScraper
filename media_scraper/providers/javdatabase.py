"""
JavDatabase Provider
从 javdatabase.com 刮削元数据（直连 HTML 刮削）。

- 搜索: https://www.javdatabase.com/?s=<CODE>
- 详情: https://www.javdatabase.com/movies/<slug>/
- 数据: 番号、标题、日期、时长、导演、工作室、厂牌、演员、类型、
        封面、样本图、预告片

注意：
- 遵守 javdatabase.com 的 robots.txt 与 ToS
- 部分地区访问可能需要代理
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
from providers.registry import register_provider

logger = logging.getLogger(__name__)

BASE_URL = "https://www.javdatabase.com"

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


@register_provider("javdatabase")
class JavDatabaseProvider(BaseProvider):
    """javdatabase.com HTML Provider。"""

    def __init__(self, proxy: Optional[str] = None, **kwargs):
        super().__init__(proxy=proxy, connector=kwargs.get("connector"), **kwargs)
        self._proxy = proxy or None

    @property
    def name(self) -> str:
        return "javdatabase"

    @property
    def display_name(self) -> str:
        return "JavDatabase"

    @property
    def supported_types(self) -> List[str]:
        return ["coded"]

    async def _fetch_html(self, url: str) -> Optional[str]:
        from providers.net import fetch_text

        text, _final = await fetch_text(self, url, headers=_DEFAULT_HEADERS)
        return text

    async def search(self, query: str, parsed: ParsedFilename) -> List[SearchResult]:
        search_url = f"{BASE_URL}/?s={query}"
        html = await self._fetch_html(search_url)
        if not html:
            return []

        soup = BeautifulSoup(html, "lxml")
        results = []
        seen = set()
        # 搜索结果以卡片网格呈现：div.card 内 p.pcard > a 指向详情页
        for card in soup.select("div.card"):
            a = card.select_one("p.pcard a[href]") or card.select_one(
                'a[href*="/movies/"]'
            )
            if not a:
                continue
            href = a["href"]
            if "/movies/" not in href:
                continue
            if href in seen:
                continue
            seen.add(href)

            title_text = a.get_text(strip=True)
            img = card.find("img")
            poster = img.get("src") if img else None

            # 提取番号与年份
            code = None
            year = None
            m = re.search(r"\b([A-Z]{2,6})-?(\d{2,5})\b", title_text.upper())
            if m:
                code = f"{m.group(1)}-{m.group(2)}"

            results.append(
                SearchResult(
                    provider=self.name,
                    title=title_text,
                    year=year,
                    url=href,
                    poster_url=poster,
                    extra={"code": code},
                )
            )
        return results

    async def get_detail(self, result: SearchResult) -> Optional[Metadata]:
        detail_url = result.url
        if not detail_url:
            return None
        html = await self._fetch_html(detail_url)
        if not html:
            return None
        return self._parse_detail(html, detail_url)

    def _parse_detail(self, html: str, detail_url: str) -> Optional[Metadata]:
        soup = BeautifulSoup(html, "lxml")

        # 信息全部位于 p.mb-1 块：<b>Label: </b>内联内容
        info = {}
        for p in soup.select("p.mb-1"):
            b = p.find("b")
            if not b:
                continue
            label = b.get_text(" ", strip=True).rstrip(":").strip().lower()
            # 内容 = 该 <b> 之后的所有兄弟节点
            parts = []
            for sib in b.next_siblings:
                parts.append(self._node_text(sib))
            value = " ".join(x for x in parts if x).strip()
            info[label] = (p, value)

        def value(label):
            return info[label][1] if label in info else ""

        # 标题
        title = value("title")
        h1 = soup.find("h1")
        if not title and h1:
            title = h1.get_text(strip=True)
        if not title:
            return None

        code = value("dvd id") or None

        year = None
        release = value("release date")
        if release:
            ym = re.search(r"(19|20)\d{2}", release)
            if ym:
                year = int(ym.group(0))

        runtime = None
        dur = value("runtime")
        if dur:
            mm = re.search(r"(\d+)", dur)
            if mm:
                runtime = int(mm.group(1))

        # 演员：只取 Idol(s)/Actress(es) 块内的链接
        actors = []
        if "idol(s)/actress(es)" in info:
            block = info["idol(s)/actress(es)"][0]
            for a in block.select('a[href*="/idols/"]'):
                name = a.get_text(strip=True)
                if name and name not in [x.name for x in actors]:
                    actors.append(Actor(name=name))

        # 类型：只取 Genre(s) 块内的链接
        genres = []
        if "genre(s)" in info:
            block = info["genre(s)"][0]
            for a in block.select('a[href*="/genres/"]'):
                g = a.get_text(strip=True)
                if g and g not in genres:
                    genres.append(g)

        # 工作室
        studio = None
        if "studio" in info:
            block = info["studio"][0]
            sa = block.select_one("a")
            studio = sa.get_text(strip=True) if sa else value("studio") or None

        # 厂牌 / 导演 / 系列
        def link_or_text(label):
            if label not in info:
                return None
            block = info[label][0]
            a = block.select_one("a")
            if a and a.get_text(strip=True):
                return a.get_text(strip=True)
            v = value(label)
            return v or None

        # 封面图：优先海报容器内的大图
        poster_url = None
        poster_img = soup.select_one("#poster-container img")
        if poster_img and poster_img.get("src"):
            poster_url = poster_img["src"]
        if not poster_url:
            og = soup.find("meta", property="og:image")
            poster_url = og["content"] if og else None

        # 样本图：排除侧栏 /vertical/ 推荐图，只保留影片自身样本
        sample_urls = []
        for a in soup.select("a[href*='sample'] img"):
            src = a.get("src") or a.get("data-src")
            if src and "/vertical/" not in src and src not in sample_urls:
                sample_urls.append(src)

        # 预告片
        trailer_url = None
        iframe = soup.select_one("iframe[src*='.mp4'], iframe[src*='sample']")
        if iframe:
            trailer_url = iframe.get("src")

        meta = Metadata(
            title=title,
            original_title=title,
            year=year,
            code=code,
            actors=actors,
            genres=genres,
            runtime=runtime,
            studio=studio,
            poster_url=poster_url,
            source_url=detail_url,
            extra={
                "content_id": value("content id") or None,
                "label": link_or_text("label"),
                "director": link_or_text("director"),
                "series": link_or_text("jav series"),
                "samples": sample_urls,
                "trailer_url": trailer_url,
            },
        )
        return meta

    @staticmethod
    def _node_text(node) -> str:
        """提取 NavigableString 或 Tag 内的文本。"""
        text = getattr(node, "get_text", None)
        if callable(text):
            return text(" ", strip=True)
        return str(node).strip()
