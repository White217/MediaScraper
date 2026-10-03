"""
Chinese detection and title priority utilities.
"""

import re
import unicodedata


_CJK_RANGE = re.compile(r'[\u4e00-\u9fff]')
# 汉字、平假名、片假名。用来区分中日文标题和纯英文标题。
_CJK_SCRIPT = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uff66-\uff9d]")


def has_cjk(text: str) -> bool:
    """标题里是否含中文或日文。"""
    if not text:
        return False
    return bool(_CJK_SCRIPT.search(text))


def has_chinese(text: str) -> bool:
    if not text:
        return False
    return bool(_CJK_RANGE.search(text))


def is_mostly_chinese(text: str, threshold: float = 0.3) -> bool:
    if not text:
        return False
    chinese_count = len(_CJK_RANGE.findall(text))
    return chinese_count / len(text) >= threshold


def extract_chinese_title(title: str, original_filename: str = None) -> str:
    """Priority: scraped Chinese title > original filename with Chinese > scraped title."""
    if not title:
        return original_filename or ""
    if has_chinese(title):
        return title
    if original_filename and has_chinese(original_filename):
        return original_filename
    return title


def sanitize_title_for_filename(title: str, max_length: int = 80) -> str:
    """Sanitize title for use in filenames."""
    if not title:
        return ""
    cleaned = re.sub(r'[<>:"/\\|?*]', '', title)
    cleaned = ''.join(ch for ch in cleaned if unicodedata.category(ch)[0] != 'C')
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip()
    return cleaned
