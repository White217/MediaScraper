"""
重命名模板引擎
Render rename templates using parsed filename data and metadata.
"""

import logging
import os
from typing import Optional

from core.models import Metadata, ParsedFilename, RenamePlan, VideoType
from core.chinese_utils import extract_chinese_title, sanitize_title_for_filename
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


# Windows 路径约束（留安全余量）：
# 单分量（文件夹/文件名）安全上限；完整路径 MAX_PATH 安全上限。
SAFE_COMPONENT_LEN = 200
SAFE_FULLPATH_LEN = 240


def fit_path_components(base_dir: str, video_stem: str, video_ext: str):
    """根据 base_dir 计算可用路径预算，返回 (target_dir, file_stem)。

    文件夹名与视频文件名默认都取 video_stem，但会在“单分量 200”和“完整路径
    240”两条约束下同时收敛，确保最终
        base / folder / file_stem+ext
    是合法、可实际移动的路径。
    """
    base_len = len(os.path.abspath(base_dir)) + 1  # 末尾分隔符
    sep = 1
    ext_len = len(video_ext)

    # 完整路径预算：剩余可分给 folder 与 file 两个分量（含一个分隔符）
    remaining = SAFE_FULLPATH_LEN - base_len - sep - ext_len
    if remaining < 30:  # base 本身已经极深，极端兜底
        remaining = 30

    # 分量上限与路径预算均分（两者各一半，最少保留 20）
    per = max(20, (remaining - 1) // 2)
    comp_cap = min(SAFE_COMPONENT_LEN, per)

    folder = video_stem[:comp_cap].strip().rstrip(".") or "video"
    file_stem = video_stem[:comp_cap].strip().rstrip(".") or "video"
    target_dir = os.path.join(base_dir, folder)
    return target_dir, file_stem


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

    # 清理非法字符
    new_name = sanitize_filename(new_name)

    # 限制文件名长度（Windows 路径总长 260 字符，预留目录和扩展名空间）
    MAX_FILENAME_LEN = 120
    name_without_ext = os.path.splitext(new_name)[0]
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
    # 子文件夹名 = 重命名后的视频主文件名（不含扩展名）。
    video_stem = sanitize_filename(os.path.splitext(new_name)[0])
    video_ext = os.path.splitext(new_name)[1]

    if create_subfolder:
        folder_name = video_stem
        target_dir = os.path.join(base_dir, folder_name)

        # 幂等保护：若视频当前已位于一个“单片独立文件夹”内（例如对同一批文件
        # 再次刮削），直接复用该文件夹，绝不在其内部再套一层。
        # 仅 in_place 模式复用；custom_dir 一律收敛到指定输出目录。
        reuse_existing = (
            output_mode != "custom_dir"
            and is_single_movie_folder(parsed, metadata, source_dir)
        )
        if reuse_existing:
            target_dir = source_dir
            file_stem = video_stem
        else:
            # 关键：Windows 单个路径分量最长约 255 字符。文件夹名与视频文件名
            # 各占一个分量，此前两者都按 MAX_FILENAME_LEN(120) 独立截断，叠加后
            # 分量并不超长；但完整路径（base + folder + file）容易超过 MAX_PATH。
            # 这里按“完整路径 + 分量”双重预算，把文件夹名与视频名一起收敛，
            # 保证 shutil.move 不会因路径过长报 WinError 3 / WinError 206。
            target_dir, file_stem = fit_path_components(
                base_dir, video_stem, video_ext
            )
    else:
        target_dir = base_dir
        file_stem = video_stem

    final_video_name = file_stem + video_ext
    target_path = os.path.join(target_dir, final_video_name)

    return RenamePlan(
        source_path=parsed.original_path,
        target_path=target_path,
        video_type=parsed.video_type,
        metadata=metadata,
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


def _is_sidecar_only(path: str) -> bool:
    """文件夹里只有刮削侧车文件（NFO/JSON/封面），没有视频或其他内容。

    预览若曾经落盘，或执行中断后留下信息文件时，应复用该文件夹，
    而不是再生成一个带 (1) 的副本。
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
        name = entry.name.lower()
        if name in _SIDECAR_NAMES or name.endswith("-poster.jpg") or name.endswith(".nfo"):
            continue
        return False
    return True


def resolve_plan_conflicts(
    plans: list[RenamePlan],
    strategy: str = "auto_suffix",
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
    used_folders: set[str] = set()
    resolved: list[RenamePlan] = []

    for plan in plans:
        folder = os.path.dirname(plan.target_path)

        def _is_conflict(f: str) -> bool:
            if f in used_folders:
                return True
            if not _folder_has_content(f):
                return False
            return not _is_sidecar_only(f)

        # 无操作：视频已经位于其目标位置（对已整理好的文件再次刮削时），
        # 直接保留该计划，既不加后缀复制，也不移动。
        src_norm = os.path.normcase(os.path.abspath(plan.source_path))
        tgt_norm = os.path.normcase(os.path.abspath(plan.target_path))
        if src_norm == tgt_norm and os.path.exists(plan.target_path):
            used_folders.add(folder)
            resolved.append(plan)
            logger.info("文件已就位，跳过移动: %s", plan.target_path)
            continue

        if not _is_conflict(folder):
            used_folders.add(folder)
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
        used_folders.add(new_folder)
        resolved.append(plan)
        logger.info("文件夹冲突已解决: %s -> %s", folder, new_folder)

    return resolved
