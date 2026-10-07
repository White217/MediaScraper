"""
重命名模板引擎的单元测试
Unit tests for rename.renamer module.
"""

import os

import pytest

from core.models import Metadata, ParsedFilename, VideoType
from core.parser import parse_filename
from rename.renamer import SAFE_COMPONENT_BYTES, render_template, build_rename_plan


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

    def test_long_cjk_title_stays_within_nas_byte_limit(self, tmp_path):
        title = "無" * 180
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        parsed = ParsedFilename(
            original_path=str(tmp_path / "CAWB-041.mp4"),
            original_name="CAWB-041.mp4",
            video_type=VideoType.CODED,
            code="CAWB-041",
            title=title,
        )
        meta = Metadata(title=title, code="CAWB-041")
        plan = build_rename_plan(
            parsed,
            templates={"coded": "{code} {title}", "fallback": "{title}"},
            metadata=meta,
            create_subfolder=True,
            output_mode="custom_dir",
            custom_output_dir=str(out_dir),
        )
        folder = os.path.basename(os.path.dirname(plan.target_path))
        filename = os.path.basename(plan.target_path)
        ext_bytes = len(".mp4".encode("utf-8"))
        assert len(folder.encode("utf-8")) <= SAFE_COMPONENT_BYTES - ext_bytes
        assert len(filename.encode("utf-8")) <= SAFE_COMPONENT_BYTES
        assert folder.startswith("CAWB-041 ")
        assert filename.startswith("CAWB-041 ")
        assert folder == os.path.splitext(filename)[0]
        assert plan.name_truncated
        assert plan.full_stem.startswith("CAWB-041 ")
        assert len(plan.full_stem) > len(folder)
        assert plan.creates_folder

    def test_long_cjk_part_label_is_kept(self, tmp_path):
        title = "無" * 180
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        parsed = ParsedFilename(
            original_path=str(tmp_path / "YSN-616.mp4"),
            original_name="YSN-616.mp4",
            video_type=VideoType.CODED,
            code="YSN-616",
            title=title,
            part_index=1,
            part_group="YSN-616",
        )
        meta = Metadata(title=title, code="YSN-616")
        plan = build_rename_plan(
            parsed,
            templates={"coded": "{code} {title}", "fallback": "{title}"},
            metadata=meta,
            create_subfolder=True,
            output_mode="custom_dir",
            custom_output_dir=str(out_dir),
        )
        filename = os.path.basename(plan.target_path)
        assert filename.startswith("YSN-616（Part 1） ")
        assert len(filename.encode("utf-8")) <= SAFE_COMPONENT_BYTES

    def test_conflict_suffix_still_fits_nas_byte_limit(self, tmp_path):
        from rename.renamer import resolve_plan_conflicts

        title = "無" * 180
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        plans = []
        for index in (1, 2):
            src = tmp_path / f"s{index}"
            src.mkdir()
            parsed = ParsedFilename(
                original_path=str(src / "CAWB-041.mp4"),
                original_name="CAWB-041.mp4",
                video_type=VideoType.CODED,
                code="CAWB-041",
                title=title,
            )
            plans.append(build_rename_plan(
                parsed,
                templates={"coded": "{code} {title}", "fallback": "{title}"},
                metadata=Metadata(title=title, code="CAWB-041"),
                create_subfolder=True,
                output_mode="custom_dir",
                custom_output_dir=str(out_dir),
            ))
        resolved = resolve_plan_conflicts(plans, "auto_suffix")
        assert resolved[1].target_path.endswith(" (1).mp4")
        for plan in resolved:
            folder = os.path.basename(os.path.dirname(plan.target_path))
            filename = os.path.basename(plan.target_path)
        assert len(folder.encode("utf-8")) <= 255
        assert len(filename.encode("utf-8")) <= 255

    def test_reserved_folder_gets_a_suffix(self, tmp_path):
        from rename.renamer import resolve_plan_conflicts

        title = "同一标题"
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        plans = []
        for index in (1, 2):
            src = tmp_path / f"s{index}"
            src.mkdir()
            parsed = ParsedFilename(
                original_path=str(src / "CAWB-041.mp4"),
                original_name="CAWB-041.mp4",
                video_type=VideoType.CODED,
                code="CAWB-041",
                title=title,
            )
            plans.append(build_rename_plan(
                parsed,
                templates={"coded": "{code} {title}", "fallback": "{title}"},
                metadata=Metadata(title=title, code="CAWB-041"),
                create_subfolder=True,
                output_mode="custom_dir",
                custom_output_dir=str(out_dir),
            ))
        reserved = {
            os.path.normcase(os.path.abspath(os.path.dirname(plans[0].target_path)))
        }
        resolved = resolve_plan_conflicts(
            [plans[1]], "auto_suffix", reserved_folders=reserved,
        )
        assert "(1)" in os.path.basename(os.path.dirname(resolved[0].target_path))


class TestLongNameChoice:
    """超长名称交给用户确认后，再按确认结果改目标路径。"""

    def _long_plan(self, tmp_path, code="CAWB-041", part_index=None):
        title = "無" * 180
        out_dir = tmp_path / "out"
        out_dir.mkdir(exist_ok=True)
        src = tmp_path / f"{code}.mp4"
        parsed = ParsedFilename(
            original_path=str(src),
            original_name=src.name,
            video_type=VideoType.CODED,
            code=code,
            title=title,
            part_index=part_index,
            part_group=code if part_index else "",
        )
        return build_rename_plan(
            parsed,
            templates={"coded": "{code} {title}", "fallback": "{title}"},
            metadata=Metadata(title=title, code=code),
            create_subfolder=True,
            output_mode="custom_dir",
            custom_output_dir=str(out_dir),
        )

    def test_short_name_is_not_flagged(self):
        parsed = ParsedFilename(
            original_path="/videos/test.mkv",
            original_name="test.mkv",
            video_type=VideoType.MOVIE,
            title="Inception",
            year=2010,
            resolution="1080p",
        )
        plan = build_rename_plan(parsed, create_subfolder=True)
        assert plan.name_truncated is False

    def test_custom_stem_replaces_folder_and_file(self, tmp_path):
        from rename.renamer import apply_long_name_choices, prepare_custom_stem

        plan = self._long_plan(tmp_path)
        stem, error = prepare_custom_stem(
            "CAWB-041 自定标题", plan.name_char_cap, plan.name_byte_cap, ".mp4",
        )
        assert error == ""
        apply_long_name_choices([plan], {plan.source_path: stem})
        assert os.path.basename(plan.target_path) == "CAWB-041 自定标题.mp4"
        assert os.path.basename(os.path.dirname(plan.target_path)) == "CAWB-041 自定标题"
        assert plan.name_truncated is False

    def test_unchanged_suggestion_keeps_plan(self, tmp_path):
        from rename.renamer import apply_long_name_choices

        plan = self._long_plan(tmp_path)
        before = plan.target_path
        suggested = os.path.splitext(os.path.basename(before))[0]
        apply_long_name_choices([plan], {plan.source_path: suggested})
        assert plan.target_path == before

    def test_overlong_custom_stem_is_rejected(self, tmp_path):
        from rename.renamer import prepare_custom_stem

        plan = self._long_plan(tmp_path)
        _stem, error = prepare_custom_stem(
            "無" * 200, plan.name_char_cap, plan.name_byte_cap, ".mp4",
        )
        assert "字节" in error
        assert prepare_custom_stem("  ", plan.name_char_cap, plan.name_byte_cap)[1] == "名称不能为空"

    def test_part_label_stays_on_the_file_only(self, tmp_path):
        from rename.renamer import apply_long_name_choices

        plan = self._long_plan(tmp_path, code="YSN-616", part_index=1)
        assert plan.name_truncated
        assert "（Part 1）" in plan.full_stem
        apply_long_name_choices(
            [plan], {plan.source_path: "YSN-616（Part 1） 短标题"},
        )
        assert os.path.basename(plan.target_path) == "YSN-616（Part 1） 短标题.mp4"
        assert os.path.basename(os.path.dirname(plan.target_path)) == "YSN-616 短标题"

    def test_cancel_leaves_long_names_unchanged(self, tmp_path):
        from core.orchestrator import Pipeline

        plan = self._long_plan(tmp_path)
        before = plan.target_path
        pipeline = Pipeline({"rename": {}, "metadata": {}})
        pipeline.rename_plans = [plan]
        pipeline.set_long_name_callback(lambda _plans: None)
        assert pipeline._confirm_truncated_names() is False
        assert plan.target_path == before
        assert plan.name_truncated

    def test_callback_choice_is_applied(self, tmp_path):
        from core.orchestrator import Pipeline

        plan = self._long_plan(tmp_path)
        pipeline = Pipeline({"rename": {}, "metadata": {}})
        pipeline.rename_plans = [plan]
        pipeline.set_long_name_callback(
            lambda plans: {plans[0].source_path: "CAWB-041 自定标题"}
        )
        pipeline._confirm_truncated_names()
        assert os.path.basename(plan.target_path) == "CAWB-041 自定标题.mp4"
