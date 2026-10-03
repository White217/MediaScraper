"""
文件名解析器
Parse video filenames into structured data: type, title, year, code, resolution, etc.

解析优先级 / Parsing priority:
1. 番号匹配 (coded, e.g. IPZZ-902、091926-001) — 在原始文件名上检测，保留连字符
2. 剧集匹配 (episode, S01E01)
3. 电影匹配 (movie, title + year + resolution)
4. CJK 标题匹配 (movie with CJK characters)
5. 兜底 → unknown
"""

import logging
import os
import re
from typing import Optional

from core.models import ParsedFilename, VideoType

logger = logging.getLogger(__name__)


# ============================================================
# 正则模式 / Regex patterns
# ============================================================

# 番号候选（带连字符）：至少两段由连字符连接，最后一段为 3+ 位纯数字
_CODE_CANDIDATE_RE = re.compile(
    r'[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*-\d{3,}'
)

# 番号候选（无连字符）：2-6 个大写字母 + 3+ 位数字，如 "ABC123"
_CODE_NO_DASH_RE = re.compile(
    r'\b([A-Z]{2,6})(\d{3,})\b'
)

# 番号验证：提取前缀(字母数字+连字符)和最终数字部分
_VALID_CODE_RE = re.compile(
    r'^([A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)-(\d+)$'
)

# 剧集: SxxExx 格式（大小写均可）
_EPISODE_SE_PATTERN = re.compile(
    r'[Ss](\d{1,2})[Ee](\d{1,2})'
)

# 电影: 标题 + 分隔符 + 年份 + 可选分辨率（不要求 $ 结尾，允许后缀噪音）
_MOVIE_YEAR_PATTERN = re.compile(
    r'^(.+?)[\s.(\[]+(\d{4})[)\].\s]*(\d{3,4}p|[24][Kk]|UHD)?'
)

# CJK 标题 + 年份 + 可选分辨率
_CJK_TITLE_YEAR_RE = re.compile(
    r'^([\u4e00-\u9fff\u3400-\u4dbf][\u4e00-\u9fff\u3400-\u4dbf\w]*\d*)'
    r'[\s.(\[]+(\d{4})[)\].\s]*'
    r'(?:(\d{3,4}p|[24][Kk]|UHD))?'
    r'\s*$'
)

# CJK 标题无年份
_CJK_TITLE_ONLY_RE = re.compile(
    r'^([\u4e00-\u9fff\u3400-\u4dbf][\u4e00-\u9fff\u3400-\u4dbf\w]*\d*)'
    r'(?:[\s.]+(\d{3,4}p|[24][Kk]|UHD))?'
    r'\s*$'
)

# P7：循环内使用的正则预编译常量
_HAS_ALPHA_RE = re.compile(r'[A-Za-z]')
_ALPHA_ONLY_RE = re.compile(r'[^A-Za-z]')
_HAS_CJK_RE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]')
_MULTI_BRACKETS_RE = re.compile(r'[()\[\]]{2,}')
_EMPTY_PAREN_RE = re.compile(r'\(\s*\)')
_EMPTY_BRACKET_RE = re.compile(r'\[\s*\]')
_RES_2160_RE = re.compile(r'\b(2160p|4[Kk]|UHD)\b', re.IGNORECASE)
_RES_1080_RE = re.compile(r'\b1080p\b', re.IGNORECASE)
_RES_720_RE = re.compile(r'\b720p\b', re.IGNORECASE)
_RES_480_RE = re.compile(r'\b480p\b', re.IGNORECASE)

# 常见噪音标签（解析前清除）
_NOISE_TAGS: set[str] = {
    'bluray', 'blu-ray', 'bdrip', 'brrip',
    'webrip', 'web-dl', 'webdl', 'web',
    'hdrip', 'dvdrip', 'dvd', 'hdtv', 'pdtv',
    'x264', 'x265', 'x266', 'h264', 'h265', 'h266',
    'hevc', 'avc', 'vp9', 'av1',
    'aac', 'ac3', 'dts', 'dts-hd', 'dtsma', 'truehd', 'atmos', 'flac', 'mp3', 'eac3',
    'repack', 'proper', 'rerip', 'remastered', 'extended', 'unrated', 'theatrical',
    'directors', 'dc', 'criterion',
    '10bit', '8bit', 'hdr', 'hdr10', 'dolby', 'dv', 'imax',
    'multi', 'dual', 'subs', 'subbed', 'dubbed',
    'internal', 'limited', 'retail',
}

# 视频文件扩展名
VIDEO_EXTENSIONS: set[str] = {
    '.mp4', '.mkv', '.avi', '.wmv', '.flv', '.mov', '.ts', '.rmvb', '.m4v', '.webm',
}


# ============================================================
# 预处理 / Preprocessing
# ============================================================

def _preprocess_name(stem: str) -> str:
    """预处理文件名：将下划线和点号统一为空格，便于后续模式匹配。"""
    result = stem.strip()
    result = result.replace('_', ' ')
    result = result.replace('.', ' ')
    return result


# ============================================================
# 核心解析 / Core parsing
# ============================================================

def parse_filename(file_path: str) -> ParsedFilename:
    """解析视频文件名，返回结构化结果。

    Args:
        file_path: 文件的完整路径或仅文件名。

    Returns:
        ParsedFilename 实例。video_type 为 UNKNOWN 表示无法识别。
    """
    original_path = file_path
    filename = os.path.basename(file_path)
    name, ext = os.path.splitext(filename)
    raw_stem = name.strip()

    if not raw_stem:
        return ParsedFilename(
            original_path=original_path,
            original_name=filename,
            video_type=VideoType.UNKNOWN,
        )

    # ---- 1. 番号匹配（在原始文件名上检测，保留连字符结构）----
    result = _try_match_coded(raw_stem, original_path, filename)
    if result:
        logger.debug("番号匹配: %s → %s", filename, result.code)
        return result

    # 后续模式使用预处理后的文本（. 和 _ 替换为空格）
    processed = _preprocess_name(raw_stem)

    # ---- 2. 剧集匹配（SxxExx）----
    result = _try_match_episode(processed, original_path, filename)
    if result:
        logger.debug("剧集匹配: %s → S%02dE%02d",
                      filename, result.season or 0, result.episode or 0)
        return result

    # ---- 3. 电影匹配（标题 + 年份 + 可选分辨率）----
    result = _try_match_movie(processed, original_path, filename)
    if result:
        logger.debug("电影匹配: %s → %s (%s)", filename, result.title, result.year)
        return result

    # ---- 4. CJK 标题匹配（中日韩文字）----
    result = _try_match_cjk(processed, original_path, filename)
    if result:
        logger.debug("CJK匹配: %s → %s", filename, result.title)
        return result

    # ---- 5. 兜底 ----
    cleaned = _clean_title(processed)
    logger.debug("兜底匹配: %s → %s (unknown)", filename, cleaned)
    return ParsedFilename(
        original_path=original_path,
        original_name=filename,
        video_type=VideoType.UNKNOWN,
        title=cleaned or raw_stem,
    )


# ============================================================
# 各类型匹配函数 / Per-type matchers
# ============================================================

def _try_match_coded(
    raw_stem: str, original_path: str, filename: str
) -> Optional[ParsedFilename]:
    """尝试匹配番号格式。

    在原始文件名上操作（保留连字符），并通过前缀长度和数字位数
    区分番号（如 IPZZ-902）和"单词-年份"（如 Parasite-2019）。
    """
    upper_raw = raw_stem.upper()
    match = _CODE_CANDIDATE_RE.search(upper_raw)
    if not match:
        # 尝试无连字符的番号匹配（如 "ABC123"）
        nd_match = _CODE_NO_DASH_RE.search(upper_raw)
        if nd_match:
            prefix_part = nd_match.group(1)
            number_part = nd_match.group(2)
            code = f"{prefix_part}-{number_part}"
            remaining = raw_stem[nd_match.end():].strip(' -')
            title = _clean_title(_preprocess_name(remaining)) if remaining else None
            return ParsedFilename(
                original_path=original_path,
                original_name=filename,
                video_type=VideoType.CODED,
                code=code,
                title=title,
                resolution=_extract_resolution(
                    _preprocess_name(raw_stem[nd_match.end():])
                ),
            )
        return None

    code_candidate = match.group(0)
    code_match = _VALID_CODE_RE.match(code_candidate)
    if not code_match:
        return None

    prefix_part = code_match.group(1)   # e.g. "IPZZ", "FC2-PPV", "091926"
    number_part = code_match.group(2)   # e.g. "902", "1234567", "001"

    # Caribbeancom 等日期番号：6 位日期 + 3 位序号，前缀没有字母，例如 091926-001。
    date_code = (
        prefix_part.isdigit()
        and len(prefix_part) == 6
        and len(number_part) == 3
    )

    if not date_code:
        # 普通番号前缀必须包含至少一个字母
        if not _HAS_ALPHA_RE.search(prefix_part):
            return None

        first_segment = prefix_part.split('-')[0]
        # 前缀首段至少 2 个字母
        if len(_ALPHA_ONLY_RE.sub('', first_segment)) < 2:
            return None

        # 如果只有两段且数字部分恰好是年份（1900-2099），
        # 则要求前缀首段 ≤ 4 字符（缩写如 IPZZ/FC2），
        # 排除长单词+年份被误识别为番号（如 PARASITE-2019, INTERSTELLAR-2014）
        if '-' not in prefix_part and len(number_part) == 4:
            year_val = int(number_part)
            if 1900 <= year_val <= 2099:
                alpha_only = _ALPHA_ONLY_RE.sub('', prefix_part)
                if len(alpha_only) > 4:
                    return None

    # 规范化番号：去掉空格和点，保留连字符
    code = prefix_part + '-' + number_part
    code = code.replace(' ', '').replace('.', '')

    # 番号后面的部分作为标题（可选）
    remaining = raw_stem[match.end():].strip(' -')
    title = _clean_title(_preprocess_name(remaining)) if remaining else None

    return ParsedFilename(
        original_path=original_path,
        original_name=filename,
        video_type=VideoType.CODED,
        code=code,
        title=title,
        resolution=_extract_resolution(
            _preprocess_name(raw_stem[match.end():])
        ),
    )


def _try_match_episode(
    processed: str, original_path: str, filename: str
) -> Optional[ParsedFilename]:
    """尝试匹配 SxxExx 剧集格式。"""
    match = _EPISODE_SE_PATTERN.search(processed)
    if not match:
        return None

    season = int(match.group(1))
    episode = int(match.group(2))

    title_part = processed[:match.start()].strip(' -.')
    title = _clean_title(title_part)
    after = processed[match.end():]
    resolution = _extract_resolution(after)

    return ParsedFilename(
        original_path=original_path,
        original_name=filename,
        video_type=VideoType.EPISODE,
        title=title,
        season=season,
        episode=episode,
        resolution=resolution,
    )


def _try_match_movie(
    processed: str, original_path: str, filename: str
) -> Optional[ParsedFilename]:
    """尝试匹配 标题 + 年份 + 分辨率 格式。"""
    match = _MOVIE_YEAR_PATTERN.match(processed)
    if not match:
        return None

    raw_title = match.group(1).strip()
    year = int(match.group(2))
    resolution = match.group(3)

    # 年份合理性校验
    if not (1900 <= year <= 2099):
        return None

    title = _clean_title(raw_title)
    if not title:
        return None

    return ParsedFilename(
        original_path=original_path,
        original_name=filename,
        video_type=VideoType.MOVIE,
        title=title,
        year=year,
        resolution=resolution,
    )


def _try_match_cjk(
    processed: str, original_path: str, filename: str
) -> Optional[ParsedFilename]:
    """尝试匹配 CJK 标题（中文/日文/韩文）。"""
    if not _HAS_CJK_RE.search(processed):
        return None

    # 带年份
    match = _CJK_TITLE_YEAR_RE.match(processed)
    if match:
        title = match.group(1).strip()
        year = int(match.group(2))
        resolution = match.group(3)
        if 1900 <= year <= 2099:
            return ParsedFilename(
                original_path=original_path,
                original_name=filename,
                video_type=VideoType.MOVIE,
                title=title,
                year=year,
                resolution=resolution,
            )

    # 不带年份
    match = _CJK_TITLE_ONLY_RE.match(processed)
    if match:
        title = match.group(1).strip()
        resolution = match.group(2)
        return ParsedFilename(
            original_path=original_path,
            original_name=filename,
            video_type=VideoType.MOVIE,
            title=title,
            resolution=resolution,
        )

    return None


def _fallback_no_year(
    processed: str, original_path: str, filename: str
) -> ParsedFilename:
    """兜底处理：无法匹配年份时。"""
    if _HAS_CJK_RE.search(processed):
        match = _CJK_TITLE_ONLY_RE.match(processed)
        if match:
            return ParsedFilename(
                original_path=original_path,
                original_name=filename,
                video_type=VideoType.MOVIE,
                title=match.group(1).strip(),
                resolution=match.group(2),
            )

    cleaned = _clean_title(processed)
    return ParsedFilename(
        original_path=original_path,
        original_name=filename,
        video_type=VideoType.UNKNOWN,
        title=cleaned or processed,
    )


# ============================================================
# 辅助函数 / Helpers
# ============================================================

def _clean_title(title: str) -> str:
    """清除标题中的噪音标签和多余空白。"""
    if not title:
        return ""

    words = title.split()
    filtered = [w for w in words if w.lower().rstrip('()') not in _NOISE_TAGS]
    result = ' '.join(filtered).strip()

    # 清除残留空括号
    result = _MULTI_BRACKETS_RE.sub('', result)
    result = _EMPTY_PAREN_RE.sub('', result)
    result = _EMPTY_BRACKET_RE.sub('', result)

    return result.strip(' -_.')


def _extract_resolution(text: str) -> Optional[str]:
    """从文本中提取分辨率标签。"""
    if not text:
        return None
    if _RES_2160_RE.search(text):
        return "2160p"
    if _RES_1080_RE.search(text):
        return "1080p"
    if _RES_720_RE.search(text):
        return "720p"
    if _RES_480_RE.search(text):
        return "480p"
    return None


def get_file_extension(file_path: str) -> str:
    """获取文件扩展名（含点号，小写）。"""
    return os.path.splitext(file_path)[1].lower()


def is_video_file(file_path: str, extra_extensions: Optional[set[str]] = None) -> bool:
    """判断文件是否为视频文件。"""
    exts = VIDEO_EXTENSIONS.copy()
    if extra_extensions:
        exts.update(extra_extensions)
    return get_file_extension(file_path) in exts
