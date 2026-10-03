"""
JavDB Provider
从 javdb.com 刮削视频元数据。

注意：
- 遵守 javdb.com 的 robots.txt 和 ToS
- 需要网络可访问 javdb.com（部分地区可能需要代理）
- javdb 有过18确认弹窗，首次访问需要携带 over18 cookie
"""

import logging
import re
from typing import List, Optional
from urllib.parse import quote

from core.models import (
    Actor,
    Metadata,
    ParsedFilename,
    SearchResult,
    VideoType,
)
from providers.base import BaseProvider
from providers.registry import register_provider

logger = logging.getLogger(__name__)

# javdb.com 基础 URL
BASE_URL = "https://javdb.com"

# 常见请求头
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    # 过18确认
    "Cookie": "over18=1",
}


_LOGIN_TITLES = {
    "登入", "登录", "ログイン", "ログインする",
    "login", "log in", "signin", "sign in",
}


def is_login_placeholder(title: str) -> bool:
    """详情页被转到登录页时，标题只剩「登入」。"""
    compact = re.sub(r"\s+", "", (title or "").strip().lower())
    return compact in _LOGIN_TITLES


def _clean_text(text: str) -> str:
    """去除 HTML 标签并清理空白。"""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _metadata_from_search(result: SearchResult) -> Optional[Metadata]:
    title = (result.title or "").strip()
    if not title or is_login_placeholder(title):
        return None
    code = (result.extra or {}).get("code") or ""
    return Metadata(
        title=title,
        original_title=title,
        code=code or None,
        source_url=result.url,
        source_provider="javdb",
        poster_url=result.poster_url or None,
    )


def _parse_javdb_detail_page(
    html: str, code: str, source_url: str
) -> Optional[Metadata]:
    """解析 javdb.com 影片详情页 HTML。"""
    if not html or len(html) < 500:
        return None

    # 检查是否为 404
    if "404" in html[:300] or "页面未找到" in html[:500]:
        return None

    # ---- 封面图 ----
    poster_url = ""
    cover = re.search(
        r'<img[^>]+class="video-cover"[^>]+src="([^"]+)"', html
    )
    if cover:
        poster_url = cover.group(1)
    else:
        # 备选
        alt = re.search(
            r'<img[^>]+src="(https://c0[^"]+)"', html
        )
        if alt:
            poster_url = alt.group(1)

    # ---- 标题 ----
    # 从 <title> 标签中提取（去掉末尾 " | JavDB"）
    title = ""
    title_tag = re.search(r"<title>([^<]+)</title>", html)
    if title_tag:
        raw = title_tag.group(1).strip()
        # 去掉 " | JavDB ..." 后缀
        title = re.sub(r"\s*\|?\s*JavDB.*", "", raw).strip()
        # 去掉开头的番号前缀
        title = re.sub(rf"^{re.escape(code)}\s+", "", title, flags=re.I).strip()

    if not title:
        # 从 strong.current-title 提取
        cur = re.search(
            r'<strong[^>]*class="current-title"[^>]*>([^<]+)</strong>', html
        )
        if cur:
            title = _clean_text(cur.group(1))

    if not title:
        title = code

    # 未登录时详情页会 302 到 /login，<title> 是「登入 | JavDB」。
    # 这不是影片标题，不能写进文件名。
    if is_login_placeholder(title):
        return None

    # ---- 演员 ----
    actors: List[Actor] = []
    # javdb 演员在 panel-block 里的 /actors/ 链接中
    # 排除分类标签链接（/actors/censored, /actors/uncensored, /actors/western）
    actor_matches = re.findall(
        r'<a[^>]*href="(/actors/[^"]+)"[^>]*>([^<]+)</a>', html
    )
    for href, name in actor_matches:
        name = name.strip()
        # 跳过分类标签
        if href in ("/actors/censored", "/actors/uncensored", "/actors/western"):
            continue
        if name and name not in [a.name for a in actors]:
            actors.append(Actor(name=name))

    # ---- 元数据字段 ----
    studio = ""
    release_date = ""
    runtime = ""
    genres: List[str] = []
    rating = 0.0

    # 解析 panel-block 键值对
    panels = re.findall(
        r'<div[^>]*class="panel-block"[^>]*>(.*?)</div>', html, re.S
    )
    for panel in panels:
        key_match = re.search(r"<strong[^>]*>([^<]+)</strong>", panel)
        if not key_match:
            continue
        key = _clean_text(key_match.group(1)).rstrip("：:").strip()
        value_html = re.sub(r"<strong[^>]*>.*?</strong>", "", panel, flags=re.S)

        # 日期（支持繁简）
        if any(k in key for k in ["日期", "date"]):
            date_match = re.search(r"(\d{4}-\d{2}-\d{2})", value_html)
            if date_match:
                release_date = date_match.group(1)

        # 片长（繁: 時長, 简: 片长）
        elif any(k in key for k in ["時長", "片长", "length"]):
            rt_match = re.search(r"(\d+)\s*分[钟鐘鍾]", value_html)
            if rt_match:
                runtime = rt_match.group(1) + "分钟"

        # 片商/工作室
        elif any(k in key for k in ["片商", "studio", "發行商"]):
            studio_match = re.search(
                r'<a[^>]*href="/studios/[^"]+"[^>]*>([^<]+)</a>', value_html
            )
            if studio_match:
                studio = studio_match.group(1).strip()
            else:
                studio = _clean_text(value_html)

        # 评分（繁: 評分, 简: 评分）
        elif any(k in key for k in ["評分", "评分", "score"]):
            score_match = re.search(r"([\d.]+)\s*分", value_html)
            if score_match:
                rating = float(score_match.group(1))

        # 类别/类型（繁: 類別, 简: 类别）
        elif any(k in key for k in ["類別", "类别", "genre", "類"]):
            # 从原始 panel HTML 中搜索标签链接（格式: /tags?c4=17）
            genre_matches = re.findall(
                r'<a[^>]*href="/tags\?[^"]*"[^>]*>([^<]+)</a>', panel
            )
            for g in genre_matches:
                g = g.strip()
                if g and len(g) < 50 and g not in genres:
                    genres.append(g)

        # 系列
        elif any(k in key for k in ["系列", "series"]):
            series_match = re.search(
                r'<a[^>]*href="/series/[^"]*"[^>]*>([^<]+)</a>', value_html
            )
            if series_match:
                genres.append(series_match.group(1).strip())

    # 年份
    year = None
    if release_date:
        ym = re.match(r"(\d{4})", release_date)
        if ym:
            year = int(ym.group(1))

    overview = f"{code} - {title}" if title != code else title
    if actors:
        overview += " / 出演: " + ", ".join(a.name for a in actors[:3])

    return Metadata(
        title=title,
        original_title=title,
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
        source_provider="javdb",
        scrape_time="",
    )


@register_provider("javdb")
class JavdbProvider(BaseProvider):
    """JavDB.com Provider。

    从 javdb.com 刮削 JAV 影片元数据。
    """

    def __init__(self, proxy: str = "", **kwargs):
        super().__init__(proxy=proxy, connector=kwargs.get("connector"), **kwargs)
        self._proxy: str = proxy

    @property
    def name(self) -> str:
        return "javdb"

    @property
    def display_name(self) -> str:
        return "JavDB Provider"

    @property
    def supported_types(self) -> List[str]:
        return ["coded", "movie"]

    def _build_query(self, parsed: ParsedFilename) -> str:
        if parsed.code:
            return parsed.code.upper()
        if parsed.title and parsed.year:
            return f"{parsed.title} {parsed.year}"
        return parsed.title or ""

    async def _fetch(self, url: str) -> Optional[str]:
        """获取页面 HTML：共享会话、每域名限速、磁盘缓存。"""
        from providers.net import fetch_text

        text, _final = await fetch_text(self, url, headers=_DEFAULT_HEADERS)
        return text

    async def search(
        self, query: str, parsed: Optional[ParsedFilename] = None
    ) -> List[SearchResult]:
        """在 javdb.com 搜索影片。"""
        search_url = f"{BASE_URL}/search?q={quote(query)}&f=all"
        logger.info("[javdb] 搜索: %s", search_url)

        html = await self._fetch(search_url)
        if not html:
            return []

        results = []
        # javdb 搜索结果: <div class="item"> <a class="box" href="/v/xxxxx">
        items = re.findall(
            r'<a\s+href="(/v/[a-zA-Z0-9]+)"\s+class="box"[^>]*title="([^"]*)"[^>]*>'
            r".*?"
            r'<div\s+class="video-title">(.*?)</div>'
            r".*?"
            r'<div\s+class="meta">\s*([\d-]+)\s*</div>',
            html,
            re.S,
        )
        for href, title_attr, title_html, date in items[:10]:
            # 提取番号（<strong> 标签内的文本）
            code_match = re.search(
                r"<strong>([A-Z0-9][A-Z0-9-]*)</strong>", title_html
            )
            code = code_match.group(1).strip() if code_match else ""
            # 清理标题
            title = _clean_text(title_html)
            title = re.sub(rf"^{re.escape(code)}\s+", "", title, flags=re.I).strip()
            if not title:
                title = _clean_text(title_attr)
                title = re.sub(
                    rf"^{re.escape(code)}\s+", "", title, flags=re.I
                ).strip()

            # 相关度：番号精确匹配最高
            relevance = 0.7
            if parsed and parsed.code and code.upper() == parsed.code.upper():
                relevance = 1.0

            results.append(
                SearchResult(
                    provider="javdb",
                    title=title,
                    year=None,
                    url=BASE_URL + href,
                    score=relevance,
                    poster_url="",
                    extra={"code": code},
                )
            )

        return results

    async def get_detail(self, result: SearchResult) -> Optional[Metadata]:
        """获取影片详情页元数据。"""
        url = result.url
        logger.info("[javdb] 获取详情: %s", url)

        html = await self._fetch(url)
        if not html:
            return None

        code = result.extra.get("code", "") if result.extra else ""
        if not code:
            code = url.rstrip("/").split("/")[-1]
        return _parse_javdb_detail_page(html, code, url)

    async def scrape(self, parsed: ParsedFilename) -> Optional[Metadata]:
        """完整刮削流程: 搜索 -> 选择最佳结果 -> 获取详情。"""
        if not self.can_handle(parsed):
            return None

        query = self._build_query(parsed)
        if not query:
            return None

        logger.info("[javdb] 搜索: %s", query)
        results = await self.search(query, parsed)
        if not results:
            logger.info("[javdb] 无结果: %s", query)
            return None

        # 选择最佳结果（番号完全匹配优先）
        best = results[0]
        if parsed.code:
            for r in results:
                r_code = r.extra.get("code", "") if r.extra else ""
                if r_code and r_code.upper() == parsed.code.upper():
                    best = r
                    break

        logger.info("[javdb] 最佳结果: %s (%s)", best.title, best.url)
        detail = await self.get_detail(best)
        if detail is not None:
            return detail
        # 搜索列表里已经有标题。详情页要求登录时，用列表标题，避免文件名变成「登入」。
        return _metadata_from_search(best)
