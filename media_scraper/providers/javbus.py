"""
JavBus Provider
从 javbus.com 刮削视频元数据。

注意：
- 遵守 javbus.com 的 robots.txt 和 ToS
- 使用 HTTPClient 限速 + 缓存 + 退避
- 需要网络可访问 javbus.com（部分地区可能需要代理）
"""

import logging
import re
from typing import List, Optional

from core.models import (
    Actor,
    Metadata,
    ParsedFilename,
    SearchResult,
    VideoType,
)
from providers.base import BaseProvider
from providers.page_parse import usable_poster_url
from providers.registry import register_provider

logger = logging.getLogger(__name__)

# javbus.com 基础 URL
BASE_URL = "https://www.javbus.com"

# 常见请求头（合理 User-Agent）
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8,ja;q=0.7",
}


def _strip_html_tags(text: str) -> str:
    """去除 HTML 标签。"""
    return re.sub(r"<[^>]+>", "", text).strip()


def _parse_javbus_movie_page(html: str, code: str) -> Optional[Metadata]:
    """解析 javbus.com 影片详情页 HTML，提取元数据。

    javbus.com 影片页结构（可能随版本变化）：
    - 封面图: <a class="bigImage" href="大图"><img src="缩略图"></a>
    - 标题:   <h3>CODE TITLE</h3> 或 <title>...</title>
    - 演员:   <div class="star-list"> 或 <a href="star/...">ACTRESS</a>
    - 信息:   <span class="header">发行日期:</span> 2024-01-01 等
    """
    if not html or len(html) < 500:
        logger.warning("HTML 内容太短，可能未成功获取页面")
        return None

    # 检查是否为 404 或错误页
    if "404" in html[:200] or "找不到" in html[:500]:
        logger.info("javbus.com 页面未找到 (code=%s)", code)
        return None

    # 软 404 检测：检查页面标题是否包含 404
    page_title_match = re.search(r"<title>([^<]+)</title>", html)
    if page_title_match and "404" in page_title_match.group(1):
        logger.info("javbus.com 软404页面 (code=%s)", code)
        return None

    # ---- 封面图 ----
    # bigImage 的 href 是大图，img src 只是缩略图。
    poster_url = ""
    cover_match = re.search(
        r'<a\s+class="bigImage"\s+href="([^"]*)"[^>]*>\s*<img\s+src="([^"]*)"',
        html,
    )
    if cover_match:
        poster_url = (cover_match.group(1) or cover_match.group(2) or "").strip()
    else:
        alt_cover = re.search(r'<img\s+src="([^"]+cover[^"]*)"', html, re.I)
        if alt_cover:
            poster_url = alt_cover.group(1).strip()
    if poster_url.startswith("//"):
        poster_url = "https:" + poster_url
    elif poster_url.startswith("/"):
        poster_url = BASE_URL + poster_url
    poster_url = usable_poster_url(poster_url)

    # ---- 标题 ----
    # javbus 主标题（<h3>）常为英文/罗马音；繁体/日文中文标题可能出现在
    # <title> 或标题区的附加行。这里收集多个候选，优先选用“含汉字”的标题，
    # 罗马音标题保留到 original_title。
    def _strip_code_prefix(text: str) -> str:
        text = _strip_html_tags(text).strip()
        cleaned = re.sub(
            r"^[-\s]*[A-Za-z]{1,6}[-\s]?\d{1,5}(?:[-\s][A-Za-z]{1,3})?\b[-\s]*",
            "",
            text,
        ).strip()
        return cleaned or text

    candidate_titles: List[str] = []

    h3_match = re.search(r"<h3>([^<]+)</h3>", html)
    if h3_match:
        candidate_titles.append(_strip_code_prefix(h3_match.group(1)))

    title_match = re.search(r"<title>([^<]+)</title>", html)
    if title_match:
        raw_title_tag = _strip_code_prefix(title_match.group(1))
        # <title> 常带 " - JavBus" 站点后缀，去掉
        raw_title_tag = re.sub(r"\s*-?\s*JavBus\s*$", "", raw_title_tag, flags=re.I).strip()
        if raw_title_tag:
            candidate_titles.append(raw_title_tag)

    # 页面可能显式给出中文/日文标题行（不同模板兼容）
    # 形如：<span class="header">中文标题：</span>中文字幕精彩作品
    # 标签结束 </span> 与实际文字之间允许有空白或其它闭合标签
    alt_match = re.search(
        r'(?:中文标题|繁體標題|繁体标题|日文標題|原題[：:]|別名[：:])[：:]'
        r'(?:</span>|</[^>]+>)\s*([^<\n]{2,120}?)(?:\s*<|\n|$)',
        html,
    )
    if alt_match:
        alt = _strip_code_prefix(alt_match.group(1))
        if alt:
            candidate_titles.insert(0, alt)

    # 去重保序
    seen_t: set = set()
    candidate_titles = [
        t for t in candidate_titles
        if t and not (t in seen_t or seen_t.add(t))
    ]

    romaji_title = next(
        (t for t in candidate_titles if not re.search(r"[\u4e00-\u9fff\u3040-\u30ff]", t)),
        candidate_titles[0] if candidate_titles else "",
    )
    # 优先选含 CJK 汉字的标题
    cjk_title = next(
        (t for t in candidate_titles if re.search(r"[\u4e00-\u9fff\u3040-\u30ff]", t)),
        "",
    )
    title = cjk_title or romaji_title or code
    original_title = romaji_title or title

    # ---- 演员列表 ----
    actors: List[Actor] = []
    # 方式1: <a href="/star/xxx" title="ACTRESS">
    actress_matches = re.findall(
        r'<a\s+href="/star/[a-z0-9]+"\s+title="([^"]+)"', html
    )
    if actress_matches:
        for name in actress_matches:
            name = name.strip()
            if name and name not in [a.name for a in actors]:
                actors.append(Actor(name=name))
    # 方式2: <div class="star-box"> 内的链接
    if not actors:
        star_box = re.search(r'class="star-box"[^>]*>(.*?)</div>', html, re.S)
        if star_box:
            names = re.findall(r'>([^<]+)</a>', star_box.group(1))
            for name in names:
                name = name.strip()
                if name:
                    actors.append(Actor(name=name))

    # ---- 元数据字段 ----
    studio = ""
    release_date = ""
    runtime = ""
    genres: List[str] = []

    # 发行日期
    date_match = re.search(
        r'<span\s+class="header">發片日期[：:]</span>\s*([\d-]+)', html
    )
    if date_match:
        release_date = date_match.group(1).strip()
    else:
        date_match = re.search(
            r'<span\s+class="header">发行日期[：:]</span>\s*([\d-]+)', html
        )
        if date_match:
            release_date = date_match.group(1).strip()

    # 片长
    length_match = re.search(
        r'<span\s+class="header">長度[：:]</span>\s*(\d+)\s*分鐘', html
    )
    if length_match:
        runtime = length_match.group(1) + "分钟"
    else:
        length_match = re.search(
            r'<span\s+class="header">长度[：:]</span>\s*(\d+)\s*分钟', html
        )
        if length_match:
            runtime = length_match.group(1) + "分钟"

    # 制作商/片商
    studio_match = re.search(
        r'<a\s+href="/studio/[a-z0-9]+"\s*title="([^"]+)"', html
    )
    if studio_match:
        studio = studio_match.group(1).strip()
    if not studio:
        studio_match = re.search(
            r'<span\s+class="header">製作商[：:]</span>\s*<a[^>]*>([^<]+)</a>', html
        )
        if studio_match:
            studio = _strip_html_tags(studio_match.group(1)).strip()

    # 分类/类型
    genre_matches = re.findall(
        r'<a\s+href="/genre/[a-z0-9/]+"[^>]*>([^<]+)</a>', html
    )
    # 去重，排除非分类链接
    seen_genres = set()
    for g in genre_matches:
        g = g.strip()
        if g and g not in seen_genres and len(g) < 50:
            genres.append(g)
            seen_genres.add(g)

    # 简介（javbus 通常没有详细简介，用标题代替）
    overview = f"{code} - {title}" if title != code else title
    if actors:
        overview += " / 出演: " + ", ".join(a.name for a in actors[:3])

    # 年份
    year = None
    if release_date:
        year_match = re.match(r"(\d{4})", release_date)
        if year_match:
            year = int(year_match.group(1))

    # 评分（javbus 通常不显示评分，设为 0）
    rating = 0.0

    # 来源 URL
    source_url = f"{BASE_URL}/{code}"

    return Metadata(
        title=title,
        original_title=original_title,
        year=year,
        code=code,
        actors=actors,
        genres=genres,
        overview=overview,
        runtime=int(re.sub(r"\D", "", runtime)) if runtime else 0,
        rating=rating,
        studio=studio,
        poster_url=poster_url,
        fanart_url="",
        source_url=source_url,
        source_provider="javbus",
        scrape_time="",
    )


@register_provider("javbus")
class JavbusProvider(BaseProvider):
    """JavBus.com Provider。

    从 javbus.com 刮削 JAV 影片元数据。
    需要网络可以访问 javbus.com。
    """

    def __init__(self, proxy: str = "", **kwargs):
        super().__init__(proxy=proxy, connector=kwargs.get("connector"), **kwargs)
        self._proxy: str = proxy

    @property
    def name(self) -> str:
        return "javbus"

    @property
    def display_name(self) -> str:
        return "JavBus Provider"

    @property
    def supported_types(self) -> List[str]:
        return ["coded", "movie"]

    def _build_query(self, parsed: ParsedFilename) -> str:
        """构建搜索关键词 —— javbus 直接用番号查询。"""
        if parsed.code:
            return parsed.code.upper()
        if parsed.title and parsed.year:
            return f"{parsed.title} {parsed.year}"
        return parsed.title or ""

    async def search(self, query: str, parsed: Optional[ParsedFilename] = None) -> List[SearchResult]:
        """在 javbus.com 搜索影片。

        javbus 的搜索 URL: /search/KEYWORD
        但番号类影片直接访问 /CODE 更准确。
        """
        # 先尝试直接访问番号页（最准确）
        code_match = re.match(r"^([A-Z0-9]+-[A-Z0-9]+)$", query, re.I)
        if code_match:
            code = code_match.group(1).upper()
            # 标准化番号格式（去掉多余空格）
            url = f"{BASE_URL}/{code}"
            logger.info("[javbus] 直接访问番号页: %s", url)

            html = await self._fetch(url)
            if html:
                metadata = _parse_javbus_movie_page(html, code)
                if metadata:
                    # P2：把已解析的完整元数据挂上，base.scrape 据此跳过重复 get_detail
                    return [
                        SearchResult(
                            provider=self.name,
                            title=metadata.title,
                            year=metadata.year,
                            url=url,
                            score=1.0,
                            metadata=metadata,
                        )
                    ]

        # 如果番号直接访问失败，尝试搜索
        search_url = f"{BASE_URL}/search/{query}"
        logger.info("[javbus] 搜索: %s", search_url)
        html = await self._fetch(search_url)
        if not html:
            return []

        results = []
        # 解析搜索结果列表
        # javbus 搜索结果页结构: <a href="/CODE" title="TITLE"><img ...></a>
        items = re.findall(
            r'<a\s+class="movie-box"\s+href="([^"]+)"\s+title="([^"]+)"',
            html,
        )
        for href, title in items[:10]:
            code_from_url = href.rstrip("/").split("/")[-1]
            result_url = BASE_URL + href if href.startswith("/") else href
            results.append(
                SearchResult(
                    provider=self.name,
                    title=title.strip(),
                    year=None,
                    url=result_url,
                    score=0.8,
                    extra={"code": code_from_url},
                )
            )

        return results

    async def get_detail(self, result: SearchResult) -> Optional[Metadata]:
        """获取影片详情页元数据。"""
        url = result.url
        logger.info("[javbus] 获取详情: %s", url)

        html = await self._fetch(url)
        if not html:
            return None

        code = result.extra.get("code") if result.extra else None
        code = code or url.rstrip("/").split("/")[-1]
        return _parse_javbus_movie_page(html, code)

    async def _fetch(self, url: str) -> Optional[str]:
        """获取页面 HTML：共享会话、每域名限速、磁盘缓存。"""
        from providers.net import fetch_text

        text, _final = await fetch_text(self, url, headers=_DEFAULT_HEADERS)
        return text

    async def scrape(self, parsed: ParsedFilename) -> Optional[Metadata]:
        """完整刮削流程: 搜索 → 选择最佳结果 → 获取详情。"""
        if not self.can_handle(parsed):
            return None

        query = self._build_query(parsed)
        if not query:
            return None

        logger.info("[javbus] 搜索: %s", query)
        results = await self.search(query, parsed)
        if not results:
            logger.info("[javbus] 无结果: %s", query)
            return None

        # 选择最佳结果（番号完全匹配优先）
        best = results[0]
        if parsed.code:
            for r in results:
                r_code = (r.extra or {}).get("code")
                if not r_code and r.metadata is not None:
                    r_code = r.metadata.code
                if r_code and str(r_code).upper() == parsed.code.upper():
                    best = r
                    break

        logger.info("[javbus] 最佳结果: %s (%s)", best.title, best.url)
        if getattr(best, "metadata", None) is not None:
            return best.metadata
        return await self.get_detail(best)
