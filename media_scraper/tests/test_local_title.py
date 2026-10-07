"""无番号中日文标题：本地归档，不查网上数据库。"""

import asyncio
import copy
import os

import providers  # noqa: F401  注册 mock provider
from core.config import DEFAULT_CONFIG
from core.models import Metadata, VideoType
from core.orchestrator import Pipeline
from core.parser import parse_filename, settle_local_download_indexes
from metadata.nfo import build_movie_nfo
from rename.renamer import build_rename_plan


TITLE = "22Hカップのどマゾのお姉さんとお泊まり生中出しセックス"


def _config():
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg.setdefault("scan", {})["min_file_size_mb"] = 0
    return cfg


def test_title_only_file_is_local_and_drops_lone_000():
    parsed = parse_filename(TITLE + "_000.mp4")
    assert parsed.video_type == VideoType.LOCAL
    assert parsed.code is None
    assert parsed.download_index == 0
    assert parsed.title == TITLE
    assert "000" not in (parsed.title or "")
    assert "前編" not in (parsed.title or "")
    assert "後編" not in (parsed.title or "")


def test_coded_movie_and_plain_names_stay_on_their_own_paths():
    assert parse_filename("IPZZ-902.mp4").video_type == VideoType.CODED
    assert parse_filename("你好李焕英.mkv").video_type == VideoType.MOVIE
    assert parse_filename("Inception.2010.1080p.mkv").video_type == VideoType.MOVIE
    assert parse_filename("asdfghjkl.mp4").video_type == VideoType.UNKNOWN


def test_split_download_indexes_stay_distinct():
    first = parse_filename(os.path.join("D:/v", TITLE + "_000.mp4"))
    second = parse_filename(os.path.join("D:/v", TITLE + "_001.mp4"))
    settle_local_download_indexes([first, second])
    assert first.title == TITLE + " 000"
    assert second.title == TITLE + " 001"
    assert first.title != second.title
    assert "前編" not in first.title
    assert "後編" not in second.title


def test_lone_001_keeps_its_number():
    parsed = parse_filename(TITLE + "_001.mp4")
    settle_local_download_indexes([parsed])
    assert parsed.title == TITLE + " 001"


def test_local_nfo_has_title_and_lock_only():
    meta = Metadata(title=TITLE, source_provider="本地", extra={"local_only": True})
    xml = build_movie_nfo(meta)
    assert f"<title>{TITLE}</title>" in xml
    assert "<lockdata>true</lockdata>" in xml
    assert "<num>" not in xml
    assert "<scraper>" not in xml
    assert "<actor>" not in xml


def test_plan_uses_title_as_folder_and_file_name(tmp_path):
    src = tmp_path / (TITLE + "_000.mp4")
    src.write_bytes(b"video")
    parsed = parse_filename(str(src))
    settle_local_download_indexes([parsed])
    meta = Metadata(title=parsed.title, source_provider="本地", extra={"local_only": True})
    plan = build_rename_plan(parsed, metadata=meta, create_subfolder=True)
    folder = os.path.basename(os.path.dirname(plan.target_path))
    filename = os.path.basename(plan.target_path)
    assert folder == TITLE
    assert filename == TITLE + ".mp4"
    assert plan.creates_folder


def test_apply_archives_locally_and_second_run_does_not_nest(tmp_path):
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    src = video_dir / (TITLE + "_000.mp4")
    src.write_bytes(b"video")
    cfg = _config()

    preview = Pipeline(cfg)
    plans = asyncio.run(preview.run_full_async(str(video_dir)))
    assert plans[0].metadata is not None
    assert plans[0].metadata.source_provider == "本地"
    assert plans[0].metadata.code is None
    assert src.is_file()
    assert not any(path.suffix.lower() == ".nfo" for path in video_dir.rglob("*"))

    asyncio.run(Pipeline(cfg).run_apply_async(str(video_dir)))
    folders = [path for path in video_dir.iterdir() if path.is_dir()]
    assert len(folders) == 1
    folder = folders[0]
    assert folder.name == TITLE
    names = {path.name for path in folder.iterdir()}
    assert TITLE + ".mp4" in names
    assert "movie.nfo" in names
    assert not any(name.lower().endswith("-poster.jpg") or name.lower() == "poster.jpg" for name in names)
    assert "(1)" not in folder.name
    nfo = (folder / "movie.nfo").read_text(encoding="utf-8")
    assert f"<title>{TITLE}</title>" in nfo
    assert "<lockdata>true</lockdata>" in nfo
    assert not src.exists()

    marker = "手工备注"
    nfo_path = folder / "movie.nfo"
    nfo_path.write_text(
        nfo_path.read_text(encoding="utf-8").replace("</movie>", marker + "</movie>"),
        encoding="utf-8",
    )
    asyncio.run(Pipeline(cfg).run_apply_async(str(video_dir)))
    folders = [path for path in video_dir.iterdir() if path.is_dir()]
    assert [path.name for path in folders] == [TITLE]
    nested = [path for path in folder.iterdir() if path.is_dir()]
    assert nested == []
    assert (folder / (TITLE + ".mp4")).is_file()
    assert marker in nfo_path.read_text(encoding="utf-8")


def test_local_file_stays_beside_coded_file(tmp_path):
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    (video_dir / (TITLE + "_000.mp4")).write_bytes(b"local")
    (video_dir / "IPZZ-902.mp4").write_bytes(b"coded")
    cfg = _config()
    for provider in cfg["providers"]:
        provider["enabled"] = provider["name"] == "mock"

    asyncio.run(Pipeline(cfg).run_apply_async(str(video_dir)))
    folders = {path.name: path for path in video_dir.iterdir() if path.is_dir()}
    assert TITLE in folders
    assert any(name.startswith("IPZZ-902") for name in folders)
    local_names = {path.name for path in folders[TITLE].iterdir()}
    assert TITLE + ".mp4" in local_names
    local_nfo = (folders[TITLE] / "movie.nfo").read_text(encoding="utf-8")
    assert "<lockdata>true</lockdata>" in local_nfo
    assert "<num>" not in local_nfo
