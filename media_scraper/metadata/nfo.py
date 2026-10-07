"""
NFO 写入器
生成兼容 Jellyfin / Emby / Kodi 的 NFO XML 文件
"""

import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional
from xml.dom import minidom

from core.models import Metadata
from providers.confidence import field_sources

logger = logging.getLogger(__name__)


def _pretty_xml(element: ET.Element) -> str:
    """将 ElementTree 格式化为带缩进的 XML 字符串"""
    rough = ET.tostring(element, encoding="unicode", xml_declaration=False)
    parsed = minidom.parseString(rough)
    pretty = parsed.toprettyxml(indent="  ", encoding=None)
    # 去掉 minidom 自动添加的 xml 声明行
    lines = [line for line in pretty.split("\n") if not line.startswith("<?xml")]
    return "\n".join(lines)


def build_movie_nfo(meta: Metadata) -> str:
    """
    生成电影 NFO（兼容 Kodi/Jellyfin/Emby）
    """
    root = ET.Element("movie")

    if (meta.extra or {}).get("local_only"):
        ET.SubElement(root, "title").text = meta.title or ""
        ET.SubElement(root, "lockdata").text = "true"
        return _pretty_xml(root)

    ET.SubElement(root, "title").text = meta.title or ""
    if meta.original_title:
        ET.SubElement(root, "originaltitle").text = meta.original_title
    if meta.year:
        ET.SubElement(root, "year").text = str(meta.year)
    if meta.code:
        ET.SubElement(root, "num").text = meta.code
    if meta.overview:
        ET.SubElement(root, "plot").text = meta.overview
    if meta.runtime:
        ET.SubElement(root, "runtime").text = str(meta.runtime)
    if meta.rating and meta.rating > 0:
        ET.SubElement(root, "rating").text = f"{meta.rating:.1f}"
    if meta.studio:
        ET.SubElement(root, "studio").text = meta.studio

    for g in meta.genres:
        ET.SubElement(root, "genre").text = g

    for actor in meta.actors:
        actor_el = ET.SubElement(root, "actor")
        ET.SubElement(actor_el, "name").text = actor.name
        if actor.role:
            ET.SubElement(actor_el, "role").text = actor.role

    if meta.source_url:
        uid = ET.SubElement(
            root, "uniqueid",
            type=meta.source_provider or "web",
            default="true",
        )
        uid.text = meta.source_url

    ET.SubElement(root, "scraper").text = meta.source_provider or ""
    _append_sources(root, meta)
    if meta.scrape_time:
        ET.SubElement(root, "scraped").text = meta.scrape_time

    return _pretty_xml(root)


def build_episode_nfo(meta: Metadata, season: int = -1, episode: int = -1) -> str:
    """生成剧集 NFO"""
    root = ET.Element("episodedetails")

    ET.SubElement(root, "title").text = meta.title or ""
    if meta.original_title:
        ET.SubElement(root, "originaltitle").text = meta.original_title
    if season >= 0:
        ET.SubElement(root, "season").text = str(season)
    if episode >= 0:
        ET.SubElement(root, "episode").text = str(episode)
    if meta.overview:
        ET.SubElement(root, "plot").text = meta.overview
    if meta.runtime:
        ET.SubElement(root, "runtime").text = str(meta.runtime)
    if meta.rating and meta.rating > 0:
        ET.SubElement(root, "rating").text = f"{meta.rating:.1f}"

    for g in meta.genres:
        ET.SubElement(root, "genre").text = g

    for actor in meta.actors:
        actor_el = ET.SubElement(root, "actor")
        ET.SubElement(actor_el, "name").text = actor.name

    ET.SubElement(root, "scraper").text = meta.source_provider or ""
    _append_sources(root, meta)

    return _pretty_xml(root)


def build_tvshow_nfo(meta: Metadata) -> str:
    """生成剧集系列级别 tvshow.nfo"""
    root = ET.Element("tvshow")

    ET.SubElement(root, "title").text = meta.title or ""
    if meta.original_title:
        ET.SubElement(root, "originaltitle").text = meta.original_title
    if meta.year:
        ET.SubElement(root, "year").text = str(meta.year)
    if meta.overview:
        ET.SubElement(root, "plot").text = meta.overview
    if meta.rating and meta.rating > 0:
        ET.SubElement(root, "rating").text = f"{meta.rating:.1f}"

    for g in meta.genres:
        ET.SubElement(root, "genre").text = g

    for actor in meta.actors:
        actor_el = ET.SubElement(root, "actor")
        ET.SubElement(actor_el, "name").text = actor.name

    _append_sources(root, meta)
    return _pretty_xml(root)


def _append_sources(root: ET.Element, meta: Metadata) -> None:
    """写入主源，以及补空字段的来源。"""
    if meta.source_provider:
        ET.SubElement(root, "source_provider").text = meta.source_provider
    confidence = (meta.extra or {}).get("confidence")
    if isinstance(confidence, int):
        ET.SubElement(root, "confidence").text = str(confidence)
    for key, value in field_sources(meta).items():
        ET.SubElement(root, key).text = value


def write_nfo(
    meta: Metadata,
    output_dir: Path,
    filename: str = "movie.nfo",
    season: int = -1,
    episode: int = -1,
) -> Optional[Path]:
    """写入 NFO 文件到指定目录"""
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        nfo_path = output_dir / filename

        if episode >= 0:
            content = build_episode_nfo(meta, season, episode)
        else:
            content = build_movie_nfo(meta)

        nfo_path.write_text(content, encoding="utf-8")
        logger.info(f"NFO 已写入: {nfo_path}")
        return nfo_path

    except Exception as e:
        logger.error(f"NFO 写入失败: {e}")
        return None
