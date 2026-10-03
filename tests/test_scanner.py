"""
目录扫描模块的单元测试
Unit tests for core.scanner module.
"""

import os
import pytest

from core.scanner import collect_videos, scan_directory


class TestScanDirectory:
    """目录扫描测试。"""

    def _create_files(self, tmp_path, files):
        """辅助函数：在 tmp_path 下创建文件。"""
        for f in files:
            fpath = os.path.join(str(tmp_path), f)
            os.makedirs(os.path.dirname(fpath), exist_ok=True)
            with open(fpath, "w") as fh:
                fh.write("test")

    def test_scan_video_files(self, tmp_path):
        self._create_files(tmp_path, [
            "movie.mp4", "show.mkv", "clip.avi",
            "readme.txt", "poster.jpg", "data.nfo",
        ])
        result = scan_directory(str(tmp_path))
        assert len(result) == 3
        basenames = [os.path.basename(p) for p in result]
        assert "movie.mp4" in basenames
        assert "show.mkv" in basenames
        assert "clip.avi" in basenames

    def test_scan_with_subdirs(self, tmp_path):
        self._create_files(tmp_path, [
            "top.mp4",
            "sub1/deep.mkv",
            "sub2/nested.avi",
        ])
        result = scan_directory(str(tmp_path), scan_subdirs=True)
        assert len(result) == 3

    def test_scan_no_subdirs(self, tmp_path):
        self._create_files(tmp_path, [
            "top.mp4",
            "sub1/deep.mkv",
        ])
        result = scan_directory(str(tmp_path), scan_subdirs=False)
        assert len(result) == 1

    def test_exclude_dirs(self, tmp_path):
        self._create_files(tmp_path, [
            "good.mp4",
            "@eaDir/thumb.mkv",
            "#recycle/deleted.avi",
        ])
        result = scan_directory(
            str(tmp_path),
            exclude_dirs={"@eaDir", "#recycle"},
        )
        assert len(result) == 1
        assert os.path.basename(result[0]) == "good.mp4"

    def test_custom_extensions(self, tmp_path):
        self._create_files(tmp_path, [
            "video.mp4", "video.xyz",
        ])
        result = scan_directory(
            str(tmp_path),
            video_extensions={".xyz"},
        )
        assert len(result) == 1
        assert result[0].endswith(".xyz")

    def test_nonexistent_directory(self):
        with pytest.raises(NotADirectoryError):
            scan_directory("/nonexistent/path")

    def test_empty_directory(self, tmp_path):
        result = scan_directory(str(tmp_path))
        assert result == []

    def test_sorted_output(self, tmp_path):
        self._create_files(tmp_path, [
            "c.mp4", "a.mp4", "b.mp4",
        ])
        result = scan_directory(str(tmp_path))
        basenames = [os.path.basename(p) for p in result]
        assert basenames == ["a.mp4", "b.mp4", "c.mp4"]


class TestCollectVideos:
    def _touch(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write("x")

    def test_mixes_files_folders_and_ignores_sidecars(self, tmp_path):
        root = str(tmp_path)
        video = os.path.join(root, "A.mp4")
        nested = os.path.join(root, "folder", "B.mkv")
        subtitle = os.path.join(root, "folder", "B.srt")
        torrent = os.path.join(root, "folder", "B.torrent")
        for path in (video, nested, subtitle, torrent):
            self._touch(path)
        picked = [video, os.path.join(root, "folder"), subtitle, torrent]
        result = collect_videos(picked)
        names = [os.path.basename(path) for path in result]
        assert names == ["A.mp4", "B.mkv"]

    def test_dedupes_file_inside_selected_folder(self, tmp_path):
        folder = os.path.join(str(tmp_path), "pack")
        video = os.path.join(folder, "C.mp4")
        self._touch(video)
        result = collect_videos([folder, video])
        assert len(result) == 1

    def test_min_file_size_drops_small_videos(self, tmp_path):
        small = tmp_path / "small.mp4"
        large = tmp_path / "large.mp4"
        small.write_bytes(b"x" * 100)
        large.write_bytes(b"x" * (2 * 1024 * 1024))
        result = collect_videos([str(tmp_path)], min_file_size_mb=1)
        names = [os.path.basename(path) for path in result]
        assert names == ["large.mp4"]
