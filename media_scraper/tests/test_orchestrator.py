"""
流水线编排模块的单元测试
Unit tests for core.orchestrator module.
"""

import asyncio
import copy
import json
import os
import threading

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

    def test_long_names_are_confirmed_after_normal_files(self, tmp_path):
        video_dir = tmp_path / "videos"
        video_dir.mkdir()
        short = video_dir / "IPZZ-902.mp4"
        long_name = "CAWB-041 " + ("無" * 120) + ".mp4"
        long = video_dir / long_name
        short.write_bytes(b"short")
        long.write_bytes(b"long")
        seen = {}

        def choose(plans):
            seen["count"] = len(plans)
            seen["short_moved"] = not short.exists()
            seen["long_waiting"] = long.exists()
            return {plans[0].source_path: "CAWB-041 短标题"}

        pipeline = Pipeline(self._mock_config())
        pipeline.set_long_name_callback(choose)
        asyncio.run(pipeline.run_apply_async(str(video_dir)))

        assert seen["count"] == 1
        assert seen["short_moved"]
        assert seen["long_waiting"]
        assert not long.exists()
        folders = [
            name for name in os.listdir(video_dir) if (video_dir / name).is_dir()
        ]
        assert any(name.startswith("CAWB-041 短标题") for name in folders)
        assert any(name.startswith("IPZZ-902") for name in folders)

    def test_cancel_long_names_keeps_finished_files(self, tmp_path):
        video_dir = tmp_path / "videos"
        video_dir.mkdir()
        short = video_dir / "IPZZ-902.mp4"
        long = video_dir / ("CAWB-041 " + ("無" * 120) + ".mp4")
        short.write_bytes(b"short")
        long.write_bytes(b"long")

        pipeline = Pipeline(self._mock_config())
        pipeline.set_long_name_callback(lambda _plans: None)
        asyncio.run(pipeline.run_apply_async(str(video_dir)))

        assert not short.exists()
        assert long.exists()
        assert any(plan.apply_status == "skipped" for plan in pipeline.rename_plans)

    def test_apply_archives_one_file_before_the_slower_scrape_finishes(self, tmp_path, monkeypatch):
        video_dir = self._create_video_dir(tmp_path, ["IPZZ-902.mp4", "SSIS-001.mp4"])
        state = {"slow_finished": False, "archived_before_slow": False}

        from providers.aggregator import ProviderAggregator
        original = ProviderAggregator.scrape

        async def scrape(self, parsed):
            if parsed.code == "SSIS-001":
                while not state["archived_before_slow"]:
                    await asyncio.sleep(0.01)
                state["slow_finished"] = True
            return await original(self, parsed)

        monkeypatch.setattr(ProviderAggregator, "scrape", scrape)
        pipeline = Pipeline(self._mock_config())

        def on_file(event):
            if event.get("status") == "success" and not state["slow_finished"]:
                state["archived_before_slow"] = True

        pipeline.set_file_event_callback(on_file)
        asyncio.run(asyncio.wait_for(pipeline.run_apply_async(video_dir), timeout=5))

        assert state["archived_before_slow"]
        assert state["slow_finished"]
        folders = [
            name for name in os.listdir(video_dir)
            if os.path.isdir(os.path.join(video_dir, name))
        ]
        assert any(name.startswith("IPZZ-902") for name in folders)
        assert any(name.startswith("SSIS-001") for name in folders)

    def test_cancel_finishes_the_group_already_archiving_and_leaves_the_rest(self, tmp_path, monkeypatch):
        video_dir = self._create_video_dir(tmp_path, ["IPZZ-902.mp4", "SSIS-001.mp4"])
        cancel = threading.Event()
        state = {"fast_archived": False}
        pipeline = Pipeline(self._mock_config())
        pipeline.set_cancel_event(cancel)

        from providers.aggregator import ProviderAggregator
        original = ProviderAggregator.scrape

        async def scrape(self, parsed):
            if parsed.code == "SSIS-001":
                while not state["fast_archived"]:
                    await asyncio.sleep(0.01)
                cancel.set()
                await asyncio.sleep(30)
            return await original(self, parsed)

        monkeypatch.setattr(ProviderAggregator, "scrape", scrape)

        def on_file(event):
            if event.get("status") == "success":
                state["fast_archived"] = True

        pipeline.set_file_event_callback(on_file)
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(asyncio.wait_for(pipeline.run_apply_async(video_dir), timeout=5))

        assert state["fast_archived"]
        assert not os.path.exists(os.path.join(video_dir, "IPZZ-902.mp4"))
        assert os.path.exists(os.path.join(video_dir, "SSIS-001.mp4"))
        sessions = [name for name in os.listdir(video_dir) if name.startswith("scrape_session_")]
        assert len(sessions) == 1
        with open(os.path.join(video_dir, sessions[0]), encoding="utf-8") as handle:
            payload = json.load(handle)
        by_name = {os.path.basename(item["source"]): item["status"] for item in payload["files"]}
        assert by_name["IPZZ-902.mp4"] == "已归档"
        assert by_name["SSIS-001.mp4"] == "未改动"
        rollbacks = [name for name in os.listdir(video_dir) if name.startswith("rollback_")]
        assert len(rollbacks) == 1
        with open(os.path.join(video_dir, rollbacks[0]), encoding="utf-8") as handle:
            moves = json.load(handle)
        assert any(
            item.get("action") == "move" and item.get("source", "").endswith("IPZZ-902.mp4")
            for item in moves
        )
        assert not any(item.get("source", "").endswith("SSIS-001.mp4") for item in moves)
