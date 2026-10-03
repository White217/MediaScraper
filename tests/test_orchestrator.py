"""
流水线编排模块的单元测试
Unit tests for core.orchestrator module.
"""

import asyncio
import copy
import os
import pytest

import providers  # noqa: F401  注册 mock provider
from core.config import DEFAULT_CONFIG
from core.orchestrator import Pipeline


class TestPipeline:
    """流水线测试。"""

    def _config(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg.setdefault("scan", {})["min_file_size_mb"] = 0
        return cfg

    def _create_video_dir(self, tmp_path, files):
        """辅助：创建测试视频目录。"""
        video_dir = tmp_path / "videos"
        video_dir.mkdir()
        for f in files:
            (video_dir / f).write_text("fake video data")
        return str(video_dir)

    def test_dry_run_basic(self, tmp_path):
        video_dir = self._create_video_dir(tmp_path, [
            "Inception.2010.1080p.mkv",
            "IPZZ-902.mp4",
            "Breaking.Bad.S05E16.720p.mkv",
        ])
        pipeline = Pipeline(self._config())
        plans = pipeline.run_dry(video_dir)

        assert len(plans) == 3
        summary = pipeline.get_summary()
        assert summary["total_files"] == 3

    def test_dry_run_export_csv(self, tmp_path):
        video_dir = self._create_video_dir(tmp_path, [
            "movie.2020.mp4",
        ])
        csv_path = str(tmp_path / "output" / "plan.csv")
        pipeline = Pipeline(self._config())
        pipeline.run_dry(video_dir, output_csv=csv_path)

        assert os.path.exists(csv_path)
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            lines = f.readlines()
        assert len(lines) == 2  # header + 1 row

    def test_dry_run_empty_dir(self, tmp_path):
        video_dir = str(tmp_path / "empty")
        os.makedirs(video_dir)
        pipeline = Pipeline(self._config())
        plans = pipeline.run_dry(video_dir)
        assert plans == []

    def test_progress_callback(self, tmp_path):
        video_dir = self._create_video_dir(tmp_path, ["test.mp4"])
        pipeline = Pipeline(self._config())
        phases_seen = []
        pipeline.set_progress_callback(
            lambda info: phases_seen.append(info.phase)
        )
        pipeline.run_dry(video_dir)
        assert "scanning" in phases_seen
        assert "parsing" in phases_seen
        assert phases_seen[-1] == "parsing"

    def _mock_config(self):
        cfg = self._config()
        for provider in cfg["providers"]:
            provider["enabled"] = provider["name"] == "mock"
        cfg.setdefault("metadata", {})["complete_title"] = False
        return cfg

    def test_preview_writes_no_sidecars_apply_one_folder(self, tmp_path):
        video_dir = self._create_video_dir(tmp_path, ["IPZZ-902.mp4"])
        cfg = self._mock_config()

        preview = Pipeline(cfg)
        asyncio.run(preview.run_full_async(video_dir))
        for dirpath, _dirs, files in os.walk(video_dir):
            for name in files:
                lower = name.lower()
                assert not lower.endswith(".nfo")
                assert lower != "metadata.json"
                assert "poster" not in lower
        assert os.path.isfile(os.path.join(video_dir, "IPZZ-902.mp4"))

        applied = Pipeline(cfg)
        asyncio.run(applied.run_apply_async(video_dir))
        folders = [
            name for name in os.listdir(video_dir)
            if os.path.isdir(os.path.join(video_dir, name))
        ]
        assert len(folders) == 1
        assert "(1)" not in folders[0]
        contents = os.listdir(os.path.join(video_dir, folders[0]))
        assert any(name.lower().endswith(".mp4") for name in contents)
        assert "movie.nfo" in contents
        assert "metadata.json" in contents

    def test_apply_reuses_existing_sidecar_folder(self, tmp_path):
        video_dir = self._create_video_dir(tmp_path, ["IPZZ-902.mp4"])
        cfg = self._mock_config()
        preview = Pipeline(cfg)
        plans = asyncio.run(preview.run_full_async(video_dir))
        folder = os.path.dirname(plans[0].target_path)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "movie.nfo"), "w", encoding="utf-8") as handle:
            handle.write("<movie/>")

        asyncio.run(Pipeline(cfg).run_apply_async(video_dir))
        folders = [
            name for name in os.listdir(video_dir)
            if os.path.isdir(os.path.join(video_dir, name))
        ]
        assert folders == [os.path.basename(folder)]
        contents = os.listdir(folder)
        assert any(name.lower().endswith(".mp4") for name in contents)
        assert "movie.nfo" in contents
