"""
JSON 元数据写入器
输出 metadata.json，包含所有刮削到的字段
"""

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from core.models import Metadata
from providers.confidence import field_sources

logger = logging.getLogger(__name__)


def metadata_to_dict(meta: Metadata) -> dict:
    """将 Metadata 转为可序列化的 dict"""
    result = {
        "title": meta.title or "",
        "original_title": meta.original_title or "",
        "year": meta.year,
        "code": meta.code or "",
        "actors": [{"name": a.name, "role": a.role or ""} for a in meta.actors],
        "genres": list(meta.genres),
        "overview": meta.overview or "",
        "runtime": meta.runtime,
        "rating": meta.rating,
        "studio": meta.studio or "",
        "poster_url": meta.poster_url or "",
        "fanart_url": meta.fanart_url or "",
        "source_url": meta.source_url or "",
        "source_provider": meta.source_provider or "",
        "scrape_time": meta.scrape_time or "",
    }
    confidence = (meta.extra or {}).get("confidence")
    if isinstance(confidence, int):
        result["confidence"] = confidence
    result.update(field_sources(meta))
    return result


def write_json(
    meta: Metadata,
    output_dir: Path,
    filename: str = "metadata.json",
) -> Optional[Path]:
    """
    写入 metadata.json

    Args:
        meta: 元数据
        output_dir: 输出目录
        filename: 输出文件名

    Returns:
        写入的文件路径，失败返回 None
    """
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / filename

        data = metadata_to_dict(meta)
        content = json.dumps(data, ensure_ascii=False, indent=2)
        json_path.write_text(content, encoding="utf-8")
        logger.info(f"JSON 已写入: {json_path}")
        return json_path

    except Exception as e:
        logger.error(f"JSON 写入失败: {e}")
        return None
