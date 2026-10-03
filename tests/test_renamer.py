"""
重命名模板引擎的单元测试
Unit tests for rename.renamer module.
"""

import os

import pytest

from core.models import Metadata, ParsedFilename, VideoType
from core.parser import parse_filename
from rename.renamer import render_template, build_rename_plan


class TestRenderTemplate:
    """模板渲染测试。"""

    def test_movie_template(self):
        parsed = ParsedFilename(
            original_path="/v/test.mkv",
            original_name="test.mkv",
            video_type=VideoType.MOVIE,
            title="Inception",
            year=2010,
            resolution="1080p",
        )
        result = render_template("{title} ({year}) [{resolution}]", parsed)
        assert result == "Inception (2010) [1080p]"

    def test_coded_template(self):
        parsed = ParsedFilename(
            original_path="/v/test.mp4",
            original_name="test.mp4",
            video_type=VideoType.CODED,
            code="IPZZ-902",
            title="Some Title",
        )
        result = render_template("{code} {title}", parsed)
        assert result == "IPZZ-902 Some Title"

    def test_caribbean_code_survives_scraped_title(self):
        parsed = parse_filename(
            "091926-001 パイズリ5種盛りでイクっ！理想のおっぱいセックス Vol.02.mp4"
        )
        meta = Metadata(
            title="パイズリ5種盛りでイクっ！理想のおっぱいセックス Vol.02",
            code="091926-001",
        )
        plan = build_rename_plan(parsed, metadata=meta, create_subfolder=False)
        name = os.path.splitext(os.path.basename(plan.target_path))[0]
        assert name.startswith("091926-001 ")
        assert "Vol.02" in name

    def test_title_starting_with_code_is_not_repeated(self):
        parsed = ParsedFilename(
            original_path="/v/FC2-PPV-1085593.mp4",
            original_name="FC2-PPV-1085593.mp4",
            video_type=VideoType.CODED,
            code="FC2-PPV-1085593",
        )
        meta = Metadata(
            title="FC2-PPV-1085593 【体操服】",
            code="FC2-PPV-1085593",
        )
        result = render_template("{code} {title}", parsed, meta)
        assert result == "FC2-PPV-1085593 【体操服】"
        assert result.count("FC2-PPV-1085593") == 1

    def test_episode_template(self):
        parsed = ParsedFilename(
            original_path="/v/test.mkv",
            original_name="test.mkv",
            video_type=VideoType.EPISODE,
            title="Breaking Bad",
            season=5,
            episode=16,
        )
        result = render_template("{title} S{season:02d}E{episode:02d}", parsed)
        assert result == "Breaking Bad S05E16"

    def test_metadata_overrides_parsed(self):
        parsed = ParsedFilename(
            original_path="/v/test.mkv",
            original_name="test.mkv",
            video_type=VideoType.MOVIE,
            title="Parsed Title",
            year=2000,
        )
        meta = Metadata(title="Real Title", year=2024)
        result = render_template("{title} ({year})", parsed, meta)
        assert result == "Real Title (2024)"

    def test_missing_variables_handled(self):
        parsed = ParsedFilename(
            original_path="/v/test.mkv",
            original_name="test.mkv",
            video_type=VideoType.MOVIE,
            title="Test",
        )
        result = render_template("{title} ({year}) [{resolution}]", parsed)
        # 空括号应被清除
        assert "()" not in result
        assert "[]" not in result
        assert "Test" in result

    def test_fallback_template(self):
        parsed = ParsedFilename(
            original_path="/v/test.mkv",
            original_name="test.mkv",
            video_type=VideoType.UNKNOWN,
            title="Random Name",
        )
        result = render_template("{title}", parsed)
        assert result == "Random Name"


class TestBuildRenamePlan:
    """重命名计划生成测试。"""

    def test_subfolder_mode(self):
        parsed = ParsedFilename(
            original_path="/videos/test.mkv",
            original_name="test.mkv",
            video_type=VideoType.MOVIE,
            title="Inception",
            year=2010,
            resolution="1080p",
        )
        plan = build_rename_plan(parsed, create_subfolder=True)
        # 应包含子文件夹
        assert "Inception (2010) [1080p]" in plan.target_path
        assert plan.target_path.endswith(".mkv")

    def test_no_subfolder_mode(self):
        import os as _os
        parsed = ParsedFilename(
            original_path="/videos/test.mkv",
            original_name="test.mkv",
            video_type=VideoType.MOVIE,
            title="Inception",
            year=2010,
            resolution="1080p",
        )
        plan = build_rename_plan(parsed, create_subfolder=False)
        # 无子文件夹，直接在原目录
        expected_dir = _os.path.join("/videos", "")
        assert plan.target_path.startswith(expected_dir) or plan.target_path.startswith("/videos/")

    def test_illegal_chars_cleaned(self):
        parsed = ParsedFilename(
            original_path="/v/test.mkv",
            original_name="test.mkv",
            video_type=VideoType.MOVIE,
            title='Movie: The "Sequel"',
            year=2024,
        )
        plan = build_rename_plan(parsed, create_subfolder=False)
        import os
        basename = os.path.basename(plan.target_path)
        assert ":" not in basename
        assert '"' not in basename
