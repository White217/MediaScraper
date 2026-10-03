"""
数据模型定义
Data models used across the media scraper pipeline.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


# ============================================================
# Enums
# ============================================================

class VideoType(Enum):
    """视频类型"""
    MOVIE = "movie"           # 电影
    CODED = "coded"           # 番号类（如 IPZZ-902）
    EPISODE = "episode"       # 剧集（S01E01 格式）
    UNKNOWN = "unknown"       # 无法识别


# ============================================================
# Input models
# ============================================================

@dataclass
class ParsedFilename:
    """文件名解析结果 / Result of filename parsing.

    Attributes:
        original_path: 原始文件完整路径
        original_name: 原始文件名（含扩展名）
        video_type:    识别出的视频类型
        code:          番号，如 "IPZZ-902"
        title:         标题（清理后的可读标题）
        year:          年份
        season:        剧集季号
        episode:       剧集集号
        resolution:    分辨率标签，如 "1080p"、"4K"
        extra_tags:    其他识别到的标签
    """
    original_path: str
    original_name: str = ""
    video_type: VideoType = VideoType.UNKNOWN
    code: Optional[str] = None
    title: Optional[str] = None
    year: Optional[int] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    resolution: Optional[str] = None
    extra_tags: list[str] = field(default_factory=list)


# ============================================================
# Metadata models
# ============================================================

@dataclass
class Actor:
    """演员信息"""
    name: str
    role: Optional[str] = None
    photo_url: Optional[str] = None


@dataclass
class Metadata:
    """刮削到的完整元数据 / Scraped metadata for a single video.

    字段设计兼容 Jellyfin / Emby / Kodi 的 NFO 格式。
    """
    title: str = ""
    original_title: Optional[str] = None
    year: Optional[int] = None
    code: Optional[str] = None
    actors: list[Actor] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    overview: Optional[str] = None          # 简介
    summary: Optional[str] = None           # 兼容别名
    runtime: Optional[int] = None           # 片长（分钟）
    rating: Optional[float] = None          # 评分
    studio: Optional[str] = None            # 制片厂/工作室
    source_url: Optional[str] = None        # 数据来源 URL
    source_provider: Optional[str] = None   # 刮削源名称
    scraper_source: Optional[str] = None    # 兼容别名
    scrape_time: Optional[str] = None       # 刮削时间 (ISO 字符串)
    scraped_at: Optional[datetime] = None   # 刮削时间 (datetime)
    poster_url: Optional[str] = None        # 封面图 URL
    fanart_url: Optional[str] = None        # 背景图 URL
    extra: dict = field(default_factory=dict)  # 扩展字段

    def __post_init__(self):
        # 兼容别名：overview <-> summary
        if self.overview is None and self.summary is not None:
            self.overview = self.summary
        if self.summary is None and self.overview is not None:
            self.summary = self.overview
        # 兼容别名：source_provider <-> scraper_source
        if self.source_provider is None and self.scraper_source is not None:
            self.source_provider = self.scraper_source
        if self.scraper_source is None and self.source_provider is not None:
            self.scraper_source = self.source_provider


@dataclass
class SearchResult:
    """Provider 搜索返回的候选结果"""
    provider: str
    title: str
    year: Optional[int] = None
    url: Optional[str] = None
    score: float = 0.0
    poster_url: Optional[str] = None
    extra: dict = field(default_factory=dict)
    # 当 search 阶段已经拿到完整元数据（如 javbus 番号直达页）时携带，
    # 用于跳过 get_detail 的重复请求（性能优化 P2）。
    metadata: Optional["Metadata"] = None


@dataclass
class ProviderResult:
    """单个刮削源的统一查询结果。

    超时、404、空页面都收成 status != hit，由聚合器继续下一个源。
    metadata 只在 status == "hit" 时有值，字段结构与 Metadata 一致。
    """
    provider: str
    metadata: Optional[Metadata] = None
    confidence: int = 0
    title_quality: int = 0
    status: str = "empty"  # hit | empty | error
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "hit" and self.metadata is not None


# ============================================================
# Pipeline models
# ============================================================

@dataclass
class RenamePlan:
    """单个文件的重命名计划 / Rename plan for one file."""
    source_path: str
    target_path: str
    video_type: VideoType
    metadata: Optional[Metadata] = None
    conflict_resolved: bool = False


@dataclass
class ProgressInfo:
    """进度信息 / Progress callback payload.

    phase 取值: scanning | parsing | searching | metadata | image | apply
    """
    phase: str
    current: int
    total: int
    percent: float          # 0.0 ~ 100.0
    message: str
    file_name: str = ""
