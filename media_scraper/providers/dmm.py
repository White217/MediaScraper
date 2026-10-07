"""
DMM / FANZA Provider
商业目录权威数据源，数据完整度最高。

接入方式（二选一，推荐第1种）：

1. FANZA Affiliate API v3（官方、稳定）
   - 端点: https://api.dmm.com/affiliate/v3/ItemList
   - 需要: api_id + affiliate_id
   - 申请: https://affiliate.dmm.com/
   - 数字内容商店可用 digital/videoa（成人）与 digital/anc（一般）
   - 该 API 支持海外访问，通常无需日本 IP

2. 直连网页（best-effort，不推荐）
   - DMM 详情页已重构为 Next.js SPA，HTML 直连解析脆弱，
     且 geo-block 日本以外，故默认不启用。

数据：标题、番号(cid/content_id)、发行日、片长、演员、导演、
厂商、厂牌、系列、类型、高清封面、样本图、预告。
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
)
from providers.base import BaseProvider
from providers.registry import register_provider

logger = logging.getLogger(__name__)

AFFILIATE_API = "https://api.dmm.com/affiliate/v3/ItemList"

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
}


@register_provider("dmm")
class DmmProvider(BaseProvider):
    """DMM/FANZA Provider。"""

    def __init__(
        self,
        api_id: Optional[str] = None,
        affiliate_id: Optional[str] = None,
        proxy: Optional[str] = None,
        floor: str = "videoa",
        **kwargs,
    ):
        super().__init__(proxy=proxy, connector=kwargs.get("connector"), **kwargs)
        self._api_id = (api_id or "").strip()
        self._affiliate_id = (affiliate_id or "").strip()
        self._proxy = proxy or None
        # videoa = 成人影像, anc = 一般数字内容
        self._floor = floor or "videoa"

        if not (self._api_id and self._affiliate_id):
            logger.warning(
                "[dmm] api_id / affiliate_id 未配置，FANZA API 无法工作。"
                "可到 https://affiliate.dmm.com/ 申请。"
            )

    @property
    def name(self) -> str:
        return "dmm"

    @property
    def display_name(self) -> str:
        return "DMM/FANZA"

    @property
    def supported_types(self) -> List[str]:
        return ["coded"]

    def can_handle(self, parsed: ParsedFilename) -> bool:
        if not (self._api_id and self._affiliate_id):
            return False
        return super().can_handle(parsed)

    async def _item_list(self, keyword: str) -> Optional[dict]:
        """调用 FANZA ItemList API。"""
        params = {
            "api_id": self._api_id,
            "affiliate_id": self._affiliate_id,
            "site": "DMM.R18" if self._floor == "videoa" else "DMM.com",
            "service": "digital",
            "floor": self._floor,
            "hits": 20,
            "keyword": keyword,
            "output": "json",
        }
        from providers.net import fetch_json

        data, _final = await fetch_json(
            self, AFFILIATE_API, params=params, headers=_DEFAULT_HEADERS
        )
        return data if isinstance(data, dict) else None

    async def search(self, query: str, parsed: ParsedFilename) -> List[SearchResult]:
        data = await self._item_list(query)
        if not data:
            return []
        items = (
            data.get("result", {}).get("items")
            if isinstance(data.get("result"), dict)
            else None
        )
        if not items:
            return []

        results = []
        for item in items:
            year = None
            rel = item.get("date") or item.get("release_date") or ""
            m = re.search(r"(19|20)\d{2}", rel)
            if m:
                year = int(m.group(0))
            img_url = None
            img = item.get("imageURL") or {}
            if isinstance(img, dict):
                img_url = img.get("large") or img.get("small")
            results.append(
                SearchResult(
                    provider=self.name,
                    title=item.get("title") or query,
                    year=year,
                    url=item.get("affiliateURL") or item.get("URL"),
                    poster_url=img_url,
                    extra={"content_id": item.get("content_id"), "cid": item.get("cid")},
                )
            )
        return results

    async def get_detail(self, result: SearchResult) -> Optional[Metadata]:
        # 用番号/标题重新精确查询一次拿完整信息
        keyword = result.extra.get("content_id") or result.title
        data = await self._item_list(keyword)
        if not data:
            return None
        items = (
            data.get("result", {}).get("items")
            if isinstance(data.get("result"), dict)
            else None
        )
        if not items:
            return None
        return self._map_item(items[0])

    def _map_item(self, item: dict) -> Optional[Metadata]:
        title = item.get("title")
        if not title:
            return None

        year = None
        rel = item.get("date") or item.get("release_date") or ""
        m = re.search(r"(19|20)\d{2}", rel)
        if m:
            year = int(m.group(0))

        runtime = None
        dur = item.get("runtime")
        if dur:
            mm = re.search(r"(\d+)", str(dur))
            if mm:
                runtime = int(mm.group(1))

        rating = None
        rev = item.get("review") or {}
        if isinstance(rev, dict) and rev.get("average"):
            try:
                rating = float(rev["average"])
            except (ValueError, TypeError):
                rating = None

        actors = []
        actresses = item.get("iteminfo", {}).get("actress") if isinstance(item.get("iteminfo"), dict) else None
        if actresses:
            for ac in actresses:
                name = ac.get("name")
                if name:
                    actors.append(Actor(name=name))

        genres = []
        g = item.get("iteminfo", {}).get("genre") if isinstance(item.get("iteminfo"), dict) else None
        if g:
            for gi in g:
                if gi.get("name") and gi["name"] not in genres:
                    genres.append(gi["name"])

        def _first(kind):
            arr = item.get("iteminfo", {}).get(kind) if isinstance(item.get("iteminfo"), dict) else None
            if arr and isinstance(arr, list):
                return arr[0].get("name")
            return None

        img_url = None
        img = item.get("imageURL") or {}
        if isinstance(img, dict):
            img_url = img.get("large") or img.get("small")

        samples = []
        sample_img = item.get("sampleImageURL") or {}
        if isinstance(sample_img, dict):
            # 常见结构: {"sample_s": {"image": [...]}}
            for v in sample_img.values():
                if isinstance(v, dict) and isinstance(v.get("image"), list):
                    samples.extend(v["image"])
                elif isinstance(v, list):
                    samples.extend(v)

        meta = Metadata(
            title=title,
            original_title=title,
            year=year,
            code=(item.get("content_id") or item.get("cid")),
            actors=actors,
            genres=genres,
            overview=item.get("iteminfo", {}).get("comment") if isinstance(item.get("iteminfo"), dict) else None,
            runtime=runtime,
            rating=rating,
            studio=_first("maker"),
            poster_url=img_url,
            source_url=item.get("affiliateURL") or item.get("URL"),
            extra={
                "label": _first("label"),
                "director": _first("director"),
                "series": _first("series"),
                "samples": samples,
                "sample_movie_url": (item.get("sampleMovieURL")),
            },
        )
        return meta

    async def scrape(self, parsed: ParsedFilename) -> Optional[Metadata]:
        if not self.can_handle(parsed):
            return None

        query = self._build_query(parsed)
        if not query:
            return None

        logger.info(f"[dmm] FANZA 搜索: {query}")
        results = await self.search(query, parsed)
        if not results:
            return None

        # 优先 content_id 精确匹配
        target = None
        if parsed.code:
            cid_norm = re.sub(r"[-_]", "", parsed.code).lower()
            for r in results:
                cid = re.sub(r"[-_]", "", (r.extra.get("content_id") or "")).lower()
                if cid.startswith(cid_norm):
                    target = r
                    break
        target = target or results[0]

        meta = await self.get_detail(target)
        if meta:
            meta.source_provider = self.name
        return meta
