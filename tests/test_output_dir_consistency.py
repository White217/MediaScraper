# -*- coding: utf-8 -*-
"""
回归测试：输出目录归档规则。
- custom_dir / in_place 均为“每个视频一个独立子文件夹”；
- 视频、NFO/JSON/封面所在目录 = 视频目标文件夹；
- 同名文件夹自动加 (1),(2)。
不联网、不实际移动文件。
"""

import os
from pathlib import Path

from core.models import (
    Actor,
    Metadata,
    ParsedFilename,
    VideoType,
)
from rename.renamer import build_rename_plan, resolve_plan_conflicts


def _make_parsed(src_path: str, code: str = "START-628") -> ParsedFilename:
    return ParsedFilename(
        original_path=src_path,
        original_name=os.path.basename(src_path),
        title=code,
        video_type=VideoType.CODED,
        code=code,
        year=None,
        resolution="1080p",
        season=None,
        episode=None,
    )


def _make_meta(code: str = "START-628") -> Metadata:
    return Metadata(
        title="测试影片",
        code=code,
        year=2026,
        poster_url="https://example.com/p.jpg",
        actors=[Actor(name="演员A")],
        genres=["测试"],
        studio="SOD",
        source_provider="javdb",
    )


def _plan(src: str, out_mode: str, out_dir: str, code: str = "START-628"):
    return build_rename_plan(
        parsed=_make_parsed(src, code),
        templates={"coded": "{code} {title}", "fallback": "{title}"},
        metadata=_make_meta(code),
        create_subfolder=True,
        output_mode=out_mode,
        custom_output_dir=out_dir,
    )


def test_custom_dir_creates_per_video_subfolder(tmp_path):
    src_dir = tmp_path / "source"
    out_dir = tmp_path / "output"
    src_dir.mkdir()
    out_dir.mkdir()
    src = str(src_dir / "START-628.mp4")

    plan = _plan(src, "custom_dir", str(out_dir))
    folder = Path(plan.target_path).parent

    # 视频必须在输出目录下的独立子文件夹里，而不是输出根目录
    assert folder.parent == out_dir
    assert folder.name == Path(plan.target_path).stem  # 文件夹名=视频主名
    # 元数据目录与视频目录一致（orchestrator 修复逻辑）
    assert Path(plan.target_path).parent == folder
    # 不在源目录
    assert src_dir not in folder.parents


def test_in_place_creates_subfolder(tmp_path):
    src_dir = tmp_path / "source"
    src_dir.mkdir()
    src = str(src_dir / "START-628.mp4")

    plan = _plan(src, "in_place", "")
    folder = Path(plan.target_path).parent
    assert folder.parent == src_dir


def test_same_name_folders_get_suffix(tmp_path):
    out_dir = tmp_path / "output"
    out_dir.mkdir()

    # 两个不同源目录、相同番号 → 重命名后同名
    src1_dir = tmp_path / "s1"
    src2_dir = tmp_path / "s2"
    src1_dir.mkdir()
    src2_dir.mkdir()
    src1 = str(src1_dir / "START-628.mp4")
    src2 = str(src2_dir / "START-628.mp4")

    plans = [
        _plan(src1, "custom_dir", str(out_dir)),
        _plan(src2, "custom_dir", str(out_dir)),
    ]
    resolved = resolve_plan_conflicts(plans, "auto_suffix")

    folders = [Path(p.target_path).parent for p in resolved]
    # 两个独立文件夹，名字不同
    assert folders[0] != folders[1]
    assert folders[1].name.endswith("(1)")
    # 第二个视频文件主名也加了序号
    assert Path(resolved[1].target_path).stem.endswith("(1)")
    # 都在输出目录下
    assert all(f.parent == out_dir for f in folders)


def test_existing_nonempty_folder_get_suffix(tmp_path):
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    src_dir = tmp_path / "s1"
    src_dir.mkdir()

    # 预先在输出目录建同名非空文件夹
    plan = _plan(str(src_dir / "START-628.mp4"), "custom_dir", str(out_dir))
    expected_folder = Path(plan.target_path).parent
    expected_folder.mkdir(parents=True)
    (expected_folder / "old.txt").write_text("x", encoding="utf-8")

    resolved = resolve_plan_conflicts([plan], "auto_suffix")
    final_folder = Path(resolved[0].target_path).parent
    assert final_folder != expected_folder
    assert final_folder.name.endswith("(1)")


def test_sidecar_only_folder_is_reused(tmp_path):
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    src_dir = tmp_path / "s1"
    src_dir.mkdir()

    plan = _plan(str(src_dir / "START-628.mp4"), "custom_dir", str(out_dir))
    expected_folder = Path(plan.target_path).parent
    expected_folder.mkdir(parents=True)
    (expected_folder / "movie.nfo").write_text("<movie/>", encoding="utf-8")
    (expected_folder / "metadata.json").write_text("{}", encoding="utf-8")
    (expected_folder / "START-628-poster.jpg").write_bytes(b"jpg")

    resolved = resolve_plan_conflicts([plan], "auto_suffix")
    assert Path(resolved[0].target_path).parent == expected_folder
    assert resolved[0].conflict_resolved is False


def test_apply_moves_subtitle_and_writes_metadata(tmp_path):
    import asyncio

    from core.pipeline_apply import apply_plans_async
    from core.progress import ProgressTracker

    src_dir = tmp_path / "source"
    out_dir = tmp_path / "output"
    src_dir.mkdir()
    out_dir.mkdir()
    video = src_dir / "START-628.mp4"
    subtitle = src_dir / "START-628.zh.srt"
    video.write_bytes(b"video")
    subtitle.write_text("subtitle", encoding="utf-8")

    plan = _plan(str(video), "custom_dir", str(out_dir))
    parsed = _make_parsed(str(video))

    class _Pipe:
        def __init__(self):
            self.tracker = ProgressTracker(total_files=1)
            self.parsed_results = [parsed]
            self.config = {}

        def _emit_file_event(self, event):
            return None

        async def _check_cancel(self):
            return None

    asyncio.run(apply_plans_async(_Pipe(), [plan]))
    folder = Path(plan.target_path).parent
    assert Path(plan.target_path).is_file()
    assert (folder / "START-628.zh.srt").is_file()
    assert not subtitle.exists()
    assert (folder / "movie.nfo").is_file()
    assert (folder / "metadata.json").is_file()
    assert not video.exists()


def test_apply_keeps_video_when_metadata_cannot_be_written(tmp_path, monkeypatch):
    import asyncio

    from core.pipeline_apply import apply_plans_async
    from core.progress import ProgressTracker

    src_dir = tmp_path / "source"
    out_dir = tmp_path / "output"
    src_dir.mkdir()
    out_dir.mkdir()
    video = src_dir / "START-628.mp4"
    video.write_bytes(b"video")
    plan = _plan(str(video), "custom_dir", str(out_dir))
    parsed = _make_parsed(str(video))

    monkeypatch.setattr("core.pipeline_apply.write_nfo", lambda *args, **kwargs: None)

    class _Pipe:
        def __init__(self):
            self.tracker = ProgressTracker(total_files=1)
            self.parsed_results = [parsed]

        def _emit_file_event(self, event):
            return None

        async def _check_cancel(self):
            return None

    asyncio.run(apply_plans_async(_Pipe(), [plan]))
    assert video.is_file()
    assert not Path(plan.target_path).exists()


def test_empty_existing_folder_is_reused(tmp_path):
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    src_dir = tmp_path / "s1"
    src_dir.mkdir()

    plan = _plan(str(src_dir / "START-628.mp4"), "custom_dir", str(out_dir))
    expected_folder = Path(plan.target_path).parent
    expected_folder.mkdir(parents=True)  # 空文件夹

    resolved = resolve_plan_conflicts([plan], "auto_suffix")
    assert Path(resolved[0].target_path).parent == expected_folder
    assert resolved[0].conflict_resolved is False
