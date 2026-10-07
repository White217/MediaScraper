"""
重命名模板引擎
Render rename templates using parsed filename data and metadata.
"""

import logging
import os
import re
from typing import Optional

from core.models import Metadata, ParsedFilename, RenamePlan, VideoType
from core.chinese_utils import extract_chinese_title, sanitize_title_for_filename
from core.parts import apply_part_label
from core.parser import parse_filename
from rename.sanitizer import resolve_conflict, sanitize_filename

logger = logging.getLogger(__name__)

# 默认用于“单片文件夹”识别的视频扩展名（可由 config.scan.video_extensions 覆盖）
DEFAULT_VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".ts", ".m4v",
    ".mpg", ".mpeg", ".webm", ".rmvb", ".iso",
}


# 默认模板
DEFAULT_TEMPLATES: dict[str, str] = {
    "movie":    "{title} ({year}) [{resolution}]",
    "coded":    "{code} {title}",
    "episode":  "{title} S{season:02d}E{episode:02d}",
    "fallback": "{title}",
}


def render_template(
    template: str,
    parsed: ParsedFilename,
    metadata: Optional[Metadata] = None,
) -> str:
    """根据模板和解析结果渲染新文件名。

    可用变量:
        {title}, {original_title}, {year}, {code}, {resolution},
        {season}, {episode}, {studio}, {rating}, {genres}

    Args:
        template: 模板字符串，如 "{title} ({year}) [{resolution}]"
        parsed:   文件名解析结果。
        metadata: 刮削到的元数据（可选，用于补充变量）。

    Returns:
        渲染后的文件名字符串（未清理非法字符）。
    """
    # 构建变量字典（优先使用中文标题，其次原文件名）
    title = ""
    if metadata and metadata.title:
        # 优先使用中文标题，如果没有中文则使用原文件名
        original_name = os.path.splitext(os.path.basename(parsed.original_path))[0] if parsed.original_path else ""
        title = extract_chinese_title(metadata.title, original_name)
    elif parsed.title:
        title = parsed.title

    year = ""
    if metadata and metadata.year:
        year = str(metadata.year)
    elif parsed.year:
        year = str(parsed.year)

    resolution = ""
    if metadata and metadata.extra.get("resolution"):
        resolution = metadata.extra["resolution"]
    elif parsed.resolution:
        resolution = parsed.resolution

    code = parsed.code or (metadata.code if metadata else "") or ""
    # 标题本身已以番号开头时，不再在模板里重复一次。
    if code and "{code}" in template and title.lower().startswith(code.lower()):
        title = title[len(code):].lstrip(" -_.:")

    original_title = ""
    if metadata and metadata.original_title:
        original_title = metadata.original_title

    studio = metadata.studio if metadata else ""
    rating = str(metadata.rating) if metadata and metadata.rating else ""
    genres = ",".join(metadata.genres) if metadata and metadata.genres else ""

    variables = {
        "title": title,
        "original_title": original_title,
        "year": year,
        "code": code,
        "resolution": resolution,
        "season": parsed.season or 0,
        "episode": parsed.episode or 0,
        "studio": studio or "",
        "rating": rating,
        "genres": genres,
    }

    try:
        result = template.format(**variables)
    except (KeyError, ValueError, IndexError) as exc:
        logger.warning("模板渲染失败 (%s): %s，使用 fallback", template, exc)
        result = title or code or os.path.splitext(parsed.original_name)[0]

    # 清理空括号和多余空格
    result = result.replace("()", "").replace("[]", "")
    result = " ".join(result.split()).strip(" -_.")

    return result


def _count_videos_in_dir(path: str, extensions) -> int:
    """统计目录直属视频文件数量（不递归）。"""
    if not os.path.isdir(path):
        return 0
    count = 0
    for entry in os.scandir(path):
        if entry.is_file() and os.path.splitext(entry.name)[1].lower() in extensions:
            count += 1
    return count


_JUNK_NAMES = {"thumbs.db", "desktop.ini", "ehthumbs.db", ".ds_store"}


def is_existing_movie_folder(
    parsed: ParsedFilename,
    metadata: Optional[Metadata],
    dir_path: str,
    video_extensions: Optional[set] = None,
) -> bool:
    """目录已经是这部影片的文件夹时，再次整理直接在里面改名，不再套一层。

    文件夹名以番号开头，里面的视频都属于这个番号，并且已经有信息文件或封面。
    上下分段放在同一个文件夹里时也算。
    """
    if not dir_path or not os.path.isdir(dir_path):
        return False

    code = (metadata.code if metadata and metadata.code else parsed.code) or ""
    if not code:
        return False
    folder_base = os.path.basename(os.path.normpath(dir_path))
    if not folder_base.upper().startswith(code.upper()):
        return False

    has_info = any(
        os.path.exists(os.path.join(dir_path, name))
        for name in ("movie.nfo", "metadata.json")
    )
    try:
        names = os.listdir(dir_path)
    except OSError:
        return False
    has_poster = any(
        name.lower().endswith("-poster.jpg") or name.lower() in ("poster.jpg", "folder.jpg")
        for name in names
    )
    if not (has_info or has_poster or folder_base.upper() == code.upper()):
        return False

    exts = video_extensions or DEFAULT_VIDEO_EXTENSIONS
    videos = [
        name for name in names
        if os.path.splitext(name)[1].lower() in exts
    ]
    if not videos:
        return False
    for name in videos:
        other = parse_filename(os.path.join(dir_path, name))
        if (other.code or "").upper() != code.upper():
            return False
    return True


def is_local_title_folder(
    parsed: ParsedFilename,
    metadata: Optional[Metadata],
    dir_path: str,
    video_extensions: Optional[set] = None,
) -> bool:
    """无番号文件已经放在同名标题文件夹里时，再次整理不再套一层。

    文件夹里如果有多段下载序号（_000 和 _001），不把它们当成同一部已归档影片。
    """
    if parsed.video_type != VideoType.LOCAL:
        return False
    if not dir_path or not os.path.isdir(dir_path):
        return False

    title = ""
    if metadata and metadata.title:
        title = metadata.title
    elif parsed.title:
        title = parsed.title
    title = sanitize_filename(title)
    if not title:
        return False
    folder = sanitize_filename(os.path.basename(os.path.normpath(dir_path)))
    if folder != title:
        return False

    exts = video_extensions or DEFAULT_VIDEO_EXTENSIONS
    try:
        names = os.listdir(dir_path)
    except OSError:
        return False
    videos = [
        name for name in names
        if os.path.splitext(name)[1].lower() in exts
    ]
    if not videos:
        return False

    parsed_videos = []
    for name in videos:
        other = parse_filename(os.path.join(dir_path, name))
        if other.video_type != VideoType.LOCAL:
            return False
        parsed_videos.append(other)

    indexes = [
        item.download_index for item in parsed_videos if item.download_index is not None
    ]
    if len(set(indexes)) > 1:
        return False

    for other in parsed_videos:
        shown = other.title or ""
        if other.download_index not in (None, 0):
            shown = f"{shown} {other.download_index:03d}".strip()
        if sanitize_filename(shown) != title:
            return False
    return True


def is_single_movie_folder(
    parsed: ParsedFilename,
    metadata: Optional[Metadata],
    dir_path: str,
    video_extensions: Optional[set] = None,
) -> bool:
    """判断 dir_path 是否已经是“单片独立文件夹”。

    用于保证在同一目录反复刮削时不再多嵌套一层。判定需同时满足：
      1. 文件夹名以番号开头（有番号时）；
      2. 该文件夹内只有一个视频文件；
      3. 已含 movie.nfo / metadata.json / 任一海报，或文件夹名即番号（强信号）。
    """
    if not dir_path or not os.path.isdir(dir_path):
        return False

    exts = video_extensions or DEFAULT_VIDEO_EXTENSIONS
    if _count_videos_in_dir(dir_path, exts) != 1:
        return False

    folder_base = os.path.basename(os.path.normpath(dir_path))
    code = (metadata.code if metadata and metadata.code else parsed.code) or ""

    # 条件 1：文件夹名以番号开头（无番号时退化为只看信息文件）
    code_match = bool(code) and folder_base.upper().startswith(code.upper())
    if code and not code_match:
        return False

    # 条件 3：已存在 Kodi 信息文件 / 海报，或文件夹名精确匹配番号
    has_info = any(
        os.path.exists(os.path.join(dir_path, name))
        for name in ("movie.nfo", "metadata.json")
    )
    has_poster = any(
        f.lower().endswith("-poster.jpg") or f.lower() in ("poster.jpg", "folder.jpg")
        for f in os.listdir(dir_path)
    )
    return has_info or has_poster or (bool(code) and folder_base.upper() == code.upper())


# 路径约束（留安全余量）：
# - 字符：Windows 单个分量约 255，完整路径 MAX_PATH 约 260。
# - 字节：Samba / ext4 的 NAME_MAX 是 255 字节。中文和日文通常 1 字 = 3 字节，
#   只按字符截断时，100 字左右的标题仍会超过 255 字节，NAS 会返回 WinError 123。
# " (999)" 是冲突后缀的最长形式，先把它的字节数从上限里扣掉。
SAFE_COMPONENT_LEN = 200
SAFE_FULLPATH_LEN = 240
_CONFLICT_SUFFIX_BYTES = len(" (999)".encode("utf-8"))
SAFE_COMPONENT_BYTES = 255 - _CONFLICT_SUFFIX_BYTES


def _utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


def _within_limits(text: str, char_cap: int, byte_cap: int) -> bool:
    return len(text) <= char_cap and _utf8_len(text) <= byte_cap


def _clip_text(text: str, char_cap: int, byte_cap: int) -> str:
    """按字符数和 UTF-8 字节数截断，不切断多字节字符。"""
    if char_cap < 1 or byte_cap < 1:
        return ""
    clipped = text[:char_cap]
    raw = clipped.encode("utf-8")
    if len(raw) > byte_cap:
        clipped = raw[:byte_cap].decode("utf-8", errors="ignore")
    return clipped.strip().rstrip(".")


def _clip_component(text: str, char_cap: int, byte_cap: int) -> str:
    return _clip_text(text, char_cap, byte_cap) or "video"


def _clip_file_stem(text: str, char_cap: int, byte_cap: int) -> str:
    """截断文件名时保留番号后的（Part N）。"""
    cleaned = text.strip()
    if _within_limits(cleaned, char_cap, byte_cap):
        return cleaned.rstrip(".") or "video"
    mark = "（Part "
    start = cleaned.find(mark)
    end = cleaned.find("）", start) if start >= 0 else -1
    if start < 0 or end < 0:
        return _clip_component(cleaned, char_cap, byte_cap)
    prefix = cleaned[: end + 1]
    rest = cleaned[end + 1 :].lstrip()
    if not _within_limits(prefix, char_cap, byte_cap):
        return _clip_text(prefix, char_cap, byte_cap) or "video"
    if not rest:
        return prefix
    char_room = char_cap - len(prefix) - 1
    byte_room = byte_cap - _utf8_len(prefix) - 1
    if char_room <= 0 or byte_room <= 0:
        return prefix
    tail = _clip_text(rest, char_room, byte_room)
    if not tail:
        return prefix
    return f"{prefix} {tail}".rstrip()


_PART_MARK = re.compile(r"（Part \d+）\s*")


def folder_name_for_stem(file_stem: str) -> str:
    """文件夹名不含分段标记，同一部影片的多段才能落在同一个目录。"""
    stripped = _PART_MARK.sub(" ", file_stem, count=1)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    return stripped or file_stem


def prepare_custom_stem(
    text: str,
    char_cap: int,
    byte_cap: int,
    video_ext: str = "",
) -> tuple[str, str]:
    """整理用户输入的主名。返回 (可用主名, 错误信息)，错误为空表示可以使用。"""
    if not text or not text.strip():
        return "", "名称不能为空"
    cleaned = sanitize_filename(text)
    if video_ext and cleaned.lower().endswith(video_ext.lower()):
        cleaned = cleaned[: -len(video_ext)].strip().rstrip(".")
    if not cleaned:
        return "", "名称不能为空"
    if not _within_limits(cleaned, char_cap, byte_cap):
        return cleaned, (
            f"仍超出长度限制（{_utf8_len(cleaned)}/{byte_cap} 字节，"
            f"{len(cleaned)}/{char_cap} 字）"
        )
    return cleaned, ""


def retarget_plan(plan: RenamePlan, file_stem: str) -> None:
    """把计划改成用户确认的主名。扩展名保持不变。"""
    file_stem = sanitize_filename(file_stem)
    if not file_stem:
        return
    ext = os.path.splitext(plan.target_path)[1]
    if plan.creates_folder:
        base = os.path.dirname(os.path.dirname(plan.target_path))
        target_dir = os.path.join(base, folder_name_for_stem(file_stem))
    else:
        target_dir = os.path.dirname(plan.target_path)
    plan.target_path = os.path.join(target_dir, file_stem + ext)
    plan.name_truncated = False


def apply_long_name_choices(plans: list, choices: dict[str, str]) -> None:
    """只改用户实际改过的名称。和自动截断结果相同的项保持原计划。"""
    for plan in plans:
        stem = choices.get(plan.source_path, "")
        if not stem:
            continue
        current = os.path.splitext(os.path.basename(plan.target_path))[0]
        if stem == current:
            continue
        retarget_plan(plan, stem)


def _component_byte_cap(video_ext: str) -> int:
    """文件名要带扩展名，文件夹与视频主名共用这个更紧的上限，避免两者截得不一致。"""
    return max(20, SAFE_COMPONENT_BYTES - _utf8_len(video_ext))


def _char_cap_for_file(base_dir: str, video_ext: str) -> int:
    base_len = len(os.path.abspath(base_dir)) + 1
    remaining = SAFE_FULLPATH_LEN - base_len - len(video_ext)
    return min(SAFE_COMPONENT_LEN, max(20, remaining))


def _subfolder_caps(base_dir: str, video_ext: str) -> tuple[int, int]:
    """新建子文件夹时，文件夹和视频主名共用的字符上限、字节上限。"""
    base_len = len(os.path.abspath(base_dir)) + 1  # 末尾分隔符
    sep = 1
    ext_len = len(video_ext)
    remaining = SAFE_FULLPATH_LEN - base_len - sep - ext_len
    if remaining < 30:  # base 本身已经极深，极端兜底
        remaining = 30
    per = max(20, (remaining - 1) // 2)
    char_cap = min(SAFE_COMPONENT_LEN, per)
    return char_cap, _component_byte_cap(video_ext)


def fit_path_components(
    base_dir: str,
    folder_stem: str,
    file_stem: str,
    video_ext: str,
):
    """根据 base_dir 计算可用路径预算，返回 (target_dir, file_stem)。

    文件夹名与视频文件名同时受三条约束：
    单分量 200 字符、完整路径 240 字符，以及 NAS 的 255 字节（已扣除冲突后缀）。
    """
    char_cap, byte_cap = _subfolder_caps(base_dir, video_ext)
    folder = _clip_component(folder_stem, char_cap, byte_cap)
    fitted_file = _clip_file_stem(file_stem, char_cap, byte_cap)
    target_dir = os.path.join(base_dir, folder)
    return target_dir, fitted_file


def build_rename_plan(
    parsed: ParsedFilename,
    templates: Optional[dict[str, str]] = None,
    metadata: Optional[Metadata] = None,
    create_subfolder: bool = True,
    output_mode: str = "in_place",
    custom_output_dir: str = "",
) -> RenamePlan:
    """为单个文件生成重命名计划。

    Args:
        parsed:           文件名解析结果。
        templates:        重命名模板字典。
        metadata:         刮削到的元数据（可选）。
        create_subfolder: 是否为视频创建子文件夹。
        output_mode:      "in_place"（原位）或 "custom_dir"（自定义目录）。
        custom_output_dir: output_mode="custom_dir" 时的目标目录。

    Returns:
        RenamePlan 实例。
    """
    tpl = templates or DEFAULT_TEMPLATES

    # 选择模板
    type_key = parsed.video_type.value
    template = tpl.get(type_key) or tpl.get("fallback", "{title}")

    # 渲染新文件名
    new_name = render_template(template, parsed, metadata)

    # 获取原始扩展名
    _, ext = os.path.splitext(parsed.original_path)
    new_name = new_name + ext

    # 清理非法字符。完整主名留在截断之前，供超长确认窗口展示。
    new_name = sanitize_filename(new_name)
    unclipped_stem = os.path.splitext(new_name)[0]

    # 限制文件名长度（Windows 路径总长 260 字符，预留目录和扩展名空间）
    MAX_FILENAME_LEN = 120
    name_without_ext = unclipped_stem
    ext = os.path.splitext(new_name)[1]
    if len(name_without_ext) > MAX_FILENAME_LEN:
        # 保留番号前缀（如果有），截断标题部分
        if metadata and metadata.code and name_without_ext.startswith(metadata.code):
            title_part = name_without_ext[len(metadata.code):].strip()
            name_without_ext = f"{metadata.code} {title_part[:MAX_FILENAME_LEN - len(metadata.code) - 1]}".strip()
        else:
            name_without_ext = name_without_ext[:MAX_FILENAME_LEN]
        new_name = name_without_ext + ext

    # 确定目标基准目录：自定义输出目录优先，否则为源文件所在目录
    source_dir = os.path.dirname(parsed.original_path)
    if output_mode == "custom_dir" and custom_output_dir:
        base_dir = custom_output_dir
    else:
        base_dir = source_dir

    # 每个视频独立子文件夹（in_place 与 custom_dir 行为一致）。
    # 文件夹名不含分段标记；同一部影片的多段共用这个名字。
    video_stem = sanitize_filename(os.path.splitext(new_name)[0])
    video_ext = os.path.splitext(new_name)[1]
    code = parsed.code or (metadata.code if metadata and metadata.code else "") or ""
    file_stem_full = video_stem
    if parsed.part_index:
        file_stem_full = sanitize_filename(
            apply_part_label(video_stem, code, parsed.part_index)
        )

    creates_folder = False
    if create_subfolder:
        # 幂等保护：视频已经在这部影片的文件夹里时直接复用，不再套一层。
        # 仅 in_place 模式复用；custom_dir 一律收敛到指定输出目录。
        reuse_existing = (
            output_mode != "custom_dir"
            and (
                is_existing_movie_folder(parsed, metadata, source_dir)
                or is_local_title_folder(parsed, metadata, source_dir)
            )
        )
        if reuse_existing:
            target_dir = source_dir
            char_cap = _char_cap_for_file(source_dir, video_ext)
            byte_cap = _component_byte_cap(video_ext)
            file_stem = _clip_file_stem(file_stem_full, char_cap, byte_cap)
        else:
            # 文件夹名与视频名一起收敛。分段标记留在番号后面。
            # 字节上限留给 NAS（255 字节），字符上限留给 Windows 路径长度。
            char_cap, byte_cap = _subfolder_caps(base_dir, video_ext)
            target_dir, file_stem = fit_path_components(
                base_dir, video_stem, file_stem_full, video_ext
            )
            creates_folder = True
    else:
        target_dir = base_dir
        char_cap = _char_cap_for_file(base_dir, video_ext)
        byte_cap = _component_byte_cap(video_ext)
        file_stem = _clip_file_stem(file_stem_full, char_cap, byte_cap)

    full_file_stem = unclipped_stem
    if parsed.part_index:
        full_file_stem = sanitize_filename(
            apply_part_label(unclipped_stem, code, parsed.part_index)
        )

    final_video_name = file_stem + video_ext
    target_path = os.path.join(target_dir, final_video_name)

    return RenamePlan(
        source_path=parsed.original_path,
        target_path=target_path,
        video_type=parsed.video_type,
        metadata=metadata,
        part_index=parsed.part_index,
        part_group=parsed.part_group,
        full_stem=full_file_stem,
        name_truncated=file_stem != full_file_stem,
        name_char_cap=char_cap,
        name_byte_cap=byte_cap,
        creates_folder=creates_folder,
    )


def _folder_has_content(path: str) -> bool:
    """文件夹是否存在且非空（含文件或子目录）。"""
    if not os.path.isdir(path):
        return False
    return any(os.scandir(path))


_SIDECAR_NAMES = {
    "movie.nfo",
    "metadata.json",
    "poster.jpg",
    "folder.jpg",
    "fanart.jpg",
    "tvshow.nfo",
}


def _is_ignored_entry(name: str) -> bool:
    lowered = name.lower()
    return (
        lowered in _SIDECAR_NAMES
        or lowered in _JUNK_NAMES
        or lowered.endswith("-poster.jpg")
        or lowered.endswith(".nfo")
    )


def _is_sidecar_only(path: str) -> bool:
    """文件夹里只有刮削侧车文件（NFO/JSON/封面），没有视频或其他内容。

    预览若曾经落盘，或执行中断后留下信息文件时，应复用该文件夹，
    而不是再生成一个带 (1) 的副本。系统缩略图文件忽略。
    """
    if not os.path.isdir(path):
        return False
    try:
        entries = list(os.scandir(path))
    except OSError:
        return False
    if not entries:
        return False
    for entry in entries:
        if entry.is_dir():
            return False
        if _is_ignored_entry(entry.name):
            continue
        return False
    return True


def _folder_has_foreign_files(path: str, allowed_sources: set[str]) -> bool:
    """文件夹里是否有不属于本次重命名、也不是侧车文件的内容。"""
    if not os.path.isdir(path):
        return False
    try:
        entries = list(os.scandir(path))
    except OSError:
        return True
    for entry in entries:
        if entry.is_dir():
            return True
        full = os.path.normcase(os.path.abspath(entry.path))
        if full in allowed_sources:
            continue
        if _is_ignored_entry(entry.name):
            continue
        return True
    return False


def resolve_plan_conflicts(
    plans: list[RenamePlan],
    strategy: str = "auto_suffix",
    reserved_folders: Optional[set[str]] = None,
    reserved_owners: Optional[dict[str, str]] = None,
) -> list[RenamePlan]:
    """按“每个视频一个独立文件夹”解决冲突。

    判定维度是目标文件夹（而非单个文件）：
    - 文件夹已被本批次使用，或磁盘上已有其他视频/无关文件 → 视为冲突；
    - 空文件夹，或只有 NFO/JSON/封面的文件夹，直接复用；
    - auto_suffix（默认）: 文件夹与视频文件主名同步加 (1),(2),...，
      使同名视频进入各自独立的文件夹；
    - skip: 跳过该计划。

    Args:
        plans:    重命名计划列表。
        strategy: "auto_suffix" | "skip"

    Returns:
        处理后的计划列表（skip 策略会移除冲突项）。
    """
    used_folders: set[str] = set(reserved_folders or ())
    folder_owners: dict[str, str] = dict(reserved_owners or {})
    sources_by_folder: dict[str, set[str]] = {}
    for plan in plans:
        folder_key = os.path.normcase(os.path.abspath(os.path.dirname(plan.target_path)))
        sources_by_folder.setdefault(folder_key, set()).add(
            os.path.normcase(os.path.abspath(plan.source_path))
        )
    resolved: list[RenamePlan] = []

    for plan in plans:
        folder = os.path.dirname(plan.target_path)
        folder_key = os.path.normcase(os.path.abspath(folder))

        def _is_conflict(f: str, current: RenamePlan = plan) -> bool:
            key = os.path.normcase(os.path.abspath(f))
            owner = folder_owners.get(key, "")
            if key in used_folders:
                return not (current.part_group and owner == current.part_group)
            if not _folder_has_content(f):
                return False
            if _is_sidecar_only(f):
                return False
            allowed = sources_by_folder.get(key, set())
            if allowed and not _folder_has_foreign_files(f, allowed):
                return False
            return True

        def _claim(folder_path: str, current: RenamePlan = plan) -> None:
            key = os.path.normcase(os.path.abspath(folder_path))
            used_folders.add(key)
            if current.part_group:
                folder_owners[key] = current.part_group

        # 无操作：视频已经位于其目标位置（对已整理好的文件再次刮削时），
        # 直接保留该计划，既不加后缀复制，也不移动。
        src_norm = os.path.normcase(os.path.abspath(plan.source_path))
        tgt_norm = os.path.normcase(os.path.abspath(plan.target_path))
        if src_norm == tgt_norm and os.path.exists(plan.target_path):
            _claim(folder)
            resolved.append(plan)
            logger.info("文件已就位，跳过移动: %s", plan.target_path)
            continue

        if not _is_conflict(folder):
            _claim(folder)
            resolved.append(plan)
            continue

        if strategy == "skip":
            logger.info("跳过同名文件夹: %s", folder)
            continue

        # auto_suffix: 文件夹 + 视频文件主名同步加序号
        counter = 1
        parent = os.path.dirname(folder)
        folder_base = os.path.basename(folder)
        video_name = os.path.basename(plan.target_path)
        video_stem, video_ext = os.path.splitext(video_name)

        new_folder = os.path.join(parent, f"{folder_base} ({counter})")
        while _is_conflict(new_folder):
            counter += 1
            new_folder = os.path.join(parent, f"{folder_base} ({counter})")
            if counter > 999:
                logger.error("重名冲突过多，跳过: %s", folder)
                new_folder = ""
                break

        if not new_folder:
            continue

        new_video = f"{video_stem} ({counter}){video_ext}"
        plan.target_path = os.path.join(new_folder, new_video)
        plan.conflict_resolved = True
        _claim(new_folder)
        resolved.append(plan)
        logger.info("文件夹冲突已解决: %s -> %s", folder, new_folder)

    return resolved
