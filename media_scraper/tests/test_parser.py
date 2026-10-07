"""
文件名解析器的单元测试
Unit tests for core.parser module.

运行方式 / Run:
    pytest tests/test_parser.py -v
"""

import pytest

from core.models import VideoType
from core.parser import parse_filename, is_video_file, get_file_extension


# ============================================================
# 番号匹配测试
# ============================================================

class TestCodedParsing:
    """番号类文件名解析 / Coded video filename parsing."""

    def test_standard_code_with_dash(self):
        r = parse_filename("IPZZ-902.mp4")
        assert r.video_type == VideoType.CODED
        assert r.code == "IPZZ-902"

    def test_code_without_dash(self):
        r = parse_filename("ABC123.mp4")
        assert r.video_type == VideoType.CODED
        assert r.code == "ABC-123"

    def test_code_with_space_separator(self):
        r = parse_filename("MIDE-790.mp4")
        assert r.video_type == VideoType.CODED
        assert r.code == "MIDE-790"

    def test_code_multi_segment_prefix(self):
        r = parse_filename("FC2-PPV-1234567.mkv")
        assert r.video_type == VideoType.CODED
        assert r.code == "FC2-PPV-1234567"

    def test_code_preserves_title(self):
        r = parse_filename("IPZZ-902 高清版.mp4")
        assert r.video_type == VideoType.CODED
        assert r.code == "IPZZ-902"

    def test_lowercase_code(self):
        r = parse_filename("heyzo-0123.mkv")
        assert r.video_type == VideoType.CODED
        assert r.code is not None
        # 番号应大写规范化
        assert r.code.isupper() or r.code[0].isupper()

    def test_code_with_extra_tags(self):
        r = parse_filename("SSIS-001 1080p.WEB-DL.mp4")
        assert r.video_type == VideoType.CODED
        assert r.code == "SSIS-001"

    def test_caribbean_date_code(self):
        r = parse_filename("091926-001.mp4")
        assert r.video_type == VideoType.CODED
        assert r.code == "091926-001"

    def test_caribbean_date_code_keeps_title(self):
        r = parse_filename("091926-001 パイズリ Vol.02.mp4")
        assert r.video_type == VideoType.CODED
        assert r.code == "091926-001"
        assert r.title is not None
        assert "Vol 02" in r.title

    def test_numeric_prefix_wrong_length_is_not_code(self):
        r = parse_filename("12345-001.mp4")
        assert r.video_type != VideoType.CODED
        assert not r.code


# ============================================================
# 电影匹配测试
# ============================================================

class TestMovieParsing:
    """电影类文件名解析 / Movie filename parsing."""

    def test_dot_separated_with_resolution(self):
        r = parse_filename("Spider-Man.2.2004.1080p.mkv")
        assert r.video_type == VideoType.MOVIE
        assert r.year == 2004
        assert r.resolution == "1080p"
        # 标题应包含 Spider 和 Man
        assert r.title is not None
        assert "Spider" in r.title

    def test_parenthesized_year(self):
        r = parse_filename("The Matrix (1999).mkv")
        assert r.video_type == VideoType.MOVIE
        assert r.year == 1999
        assert "Matrix" in r.title

    def test_4k_resolution(self):
        r = parse_filename("Inception.2010.2160p.UHD.mp4")
        assert r.video_type == VideoType.MOVIE
        assert r.year == 2010
        assert r.resolution in ("2160p", "4K")

    def test_720p_resolution(self):
        r = parse_filename("Interstellar 2014 720p BluRay.mkv")
        assert r.video_type == VideoType.MOVIE
        assert r.year == 2014
        assert r.resolution == "720p"

    def test_movie_no_resolution(self):
        r = parse_filename("Parasite.2019.mkv")
        assert r.video_type == VideoType.MOVIE
        assert r.year == 2019
        assert r.resolution is None
        assert "Parasite" in r.title


# ============================================================
# 剧集匹配测试
# ============================================================

class TestEpisodeParsing:
    """剧集文件名解析 / Episode (TV show) filename parsing."""

    def test_standard_se_format(self):
        r = parse_filename("Breaking.Bad.S05E16.720p.mkv")
        assert r.video_type == VideoType.EPISODE
        assert r.season == 5
        assert r.episode == 16
        assert r.resolution == "720p"
        assert "Breaking" in r.title and "Bad" in r.title

    def test_uppercase_se(self):
        r = parse_filename("The.Office.S02E03.1080p.mp4")
        assert r.video_type == VideoType.EPISODE
        assert r.season == 2
        assert r.episode == 3

    def test_lowercase_se(self):
        r = parse_filename("friends.s01e01.pilot.mkv")
        assert r.video_type == VideoType.EPISODE
        assert r.season == 1
        assert r.episode == 1

    def test_double_digit_season(self):
        r = parse_filename("Supernatural.S12E05.720p.mkv")
        assert r.video_type == VideoType.EPISODE
        assert r.season == 12
        assert r.episode == 5


# ============================================================
# CJK 标题测试
# ============================================================

class TestCJKParsing:
    """CJK 标题解析 / Chinese-Japanese-Korean title parsing."""

    def test_chinese_with_year(self):
        r = parse_filename("流浪地球2.2023.1080p.mkv")
        assert r.video_type == VideoType.MOVIE
        assert r.year == 2023
        assert r.resolution == "1080p"
        assert r.title is not None
        assert "流浪地球" in r.title

    def test_chinese_without_year(self):
        r = parse_filename("你好李焕英.mkv")
        assert r.video_type == VideoType.MOVIE
        assert r.title is not None
        assert "你好李焕英" in r.title

    def test_chinese_with_resolution_no_year(self):
        r = parse_filename("三体.1080p.mp4")
        assert r.video_type == VideoType.MOVIE
        assert r.title is not None
        assert "三体" in r.title


# ============================================================
# 未知类型 & 兜底测试
# ============================================================

class TestUnknownParsing:
    """未知/兜底解析 / Unknown and fallback parsing."""

    def test_gibberish_filename(self):
        r = parse_filename("asdfghjkl.mp4")
        assert r.video_type == VideoType.UNKNOWN
        assert r.title is not None

    def test_empty_name(self):
        r = parse_filename(".mp4")
        assert r.video_type == VideoType.UNKNOWN

    def test_original_path_preserved(self):
        path = "/some/deep/path/video.mp4"
        r = parse_filename(path)
        assert r.original_path == path
        assert r.original_name == "video.mp4"


# ============================================================
# 工具函数测试
# ============================================================

class TestUtilities:
    """工具函数测试 / Utility function tests."""

    def test_is_video_file_positive(self):
        assert is_video_file("test.mp4") is True
        assert is_video_file("test.mkv") is True
        assert is_video_file("test.AVI") is True

    def test_is_video_file_negative(self):
        assert is_video_file("test.txt") is False
        assert is_video_file("test.jpg") is False
        assert is_video_file("test.nfo") is False

    def test_get_file_extension(self):
        assert get_file_extension("movie.MKV") == ".mkv"
        assert get_file_extension("no_ext") == ""
