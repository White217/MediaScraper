"""同一影片分段：识别、命名，以及 YSN-616 这个真实目录的预览。"""

import json
import os

from core.models import Metadata
from core.parser import parse_filename
from core.parts import assign_part_indexes, detect_part_mark
from rename.renamer import build_rename_plan, resolve_plan_conflicts

# 可选：本地真实目录预览。勿把个人路径写进仓库。
YSN_DIR = os.environ.get("MEDIASCRAPER_TEST_YSN_DIR", "")
YSN_UPPER = (
    "YSN-616 【FANZA限定】やべーほどカワイイ妹を貪りたい 横宮七海 パンティと生写真付き（上）.MP4"
)
YSN_LOWER = (
    "YSN-616 【FANZA限定】やべーほどカワイイ妹を貪りたい 横宮七海 パンティと生写真付き (下).MP4"
)
YSN_TITLE = "【FANZA限定】やべーほどカワイイ妹を貪りたい 横宮七海 パンティと生写真付き"


def _plans_for(names, directory, metadata=None):
    parsed = [parse_filename(os.path.join(directory, name)) for name in names]
    assign_part_indexes(parsed)
    plans = [
        build_rename_plan(
            item,
            templates={"coded": "{code} {title}", "fallback": "{title}"},
            metadata=metadata,
            create_subfolder=True,
            output_mode="in_place",
        )
        for item in parsed
    ]
    return parsed, resolve_plan_conflicts(plans)


def test_ysn616_upper_and_lower_share_one_folder(tmp_path):
    folder = tmp_path / "YSN-616 样例"
    folder.mkdir()
    (folder / YSN_UPPER).write_bytes(b"u")
    (folder / YSN_LOWER).write_bytes(b"d")
    (folder / "movie.nfo").write_text("<movie/>", encoding="utf-8")

    meta = Metadata(title=YSN_TITLE, code="YSN-616", year=2024)
    parsed, plans = _plans_for([YSN_UPPER, YSN_LOWER], str(folder), meta)

    by_source = {os.path.basename(item.original_path): item.part_index for item in parsed}
    assert by_source[YSN_UPPER] == 1
    assert by_source[YSN_LOWER] == 2

    folders = {os.path.dirname(plan.target_path) for plan in plans}
    assert folders == {str(folder)}
    names = sorted(os.path.basename(plan.target_path) for plan in plans)
    assert names[0].startswith("YSN-616（Part 1） ")
    assert names[1].startswith("YSN-616（Part 2） ")
    assert all(YSN_TITLE in name for name in names)
    assert all("（上）" not in name and "(下)" not in name for name in names)
    assert all(not plan.conflict_resolved for plan in plans)


def test_real_ysn616_folder_preview_does_not_move():
    if not os.path.isdir(YSN_DIR):
        return
    before = set(os.listdir(YSN_DIR))
    meta_path = os.path.join(YSN_DIR, "metadata.json")
    meta = None
    if os.path.isfile(meta_path):
        with open(meta_path, encoding="utf-8") as handle:
            payload = json.load(handle)
        meta = Metadata(
            title=payload.get("title") or "",
            code=payload.get("code") or "YSN-616",
            year=payload.get("year"),
        )
    videos = [name for name in before if name.lower().endswith(".mp4")]
    parsed, plans = _plans_for(videos, YSN_DIR, meta)

    assert os.listdir(YSN_DIR) and set(os.listdir(YSN_DIR)) == before
    assert {item.part_index for item in parsed} == {1, 2}
    folders = {os.path.dirname(plan.target_path) for plan in plans}
    assert folders == {YSN_DIR}
    stems = [os.path.splitext(os.path.basename(plan.target_path))[0] for plan in plans]
    assert any(stem.startswith("YSN-616（Part 1） ") for stem in stems)
    assert any(stem.startswith("YSN-616（Part 2） ") for stem in stems)


def test_duplicate_copy_suffix_is_not_a_part(tmp_path):
    folder = tmp_path / "inbox"
    folder.mkdir()
    names = ["ABCD-123.mp4", "ABCD-123(1).mp4"]
    for name in names:
        (folder / name).write_bytes(b"v")
    parsed, plans = _plans_for(names, str(folder))
    assert all(item.part_index is None for item in parsed)
    assert len({os.path.dirname(plan.target_path) for plan in plans}) == 2


def test_letter_gap_is_not_grouped(tmp_path):
    folder = tmp_path / "inbox"
    folder.mkdir()
    names = ["START-628-A.mp4", "START-628-C.mp4"]
    parsed, plans = _plans_for(names, str(folder))
    assert all(item.part_index is None for item in parsed)
    assert len({os.path.dirname(plan.target_path) for plan in plans}) == 2


def test_contiguous_numbers_and_letters(tmp_path):
    folder = tmp_path / "inbox"
    folder.mkdir()
    names = ["START-628-1.mp4", "START-628-2.mp4", "IPZZ-902a.mp4", "IPZZ-902b.mp4"]
    for name in names:
        (folder / name).write_bytes(b"v")
    parsed, plans = _plans_for(names, str(folder))
    indexes = {item.original_name: item.part_index for item in parsed}
    assert indexes["START-628-1.mp4"] == 1
    assert indexes["START-628-2.mp4"] == 2
    assert indexes["IPZZ-902a.mp4"] == 1
    assert indexes["IPZZ-902b.mp4"] == 2
    folders = {}
    for plan in plans:
        folders.setdefault(os.path.dirname(plan.target_path), set()).add(plan.part_index)
    assert {1, 2} in folders.values()
    assert list(folders.values()).count({1, 2}) == 2


def test_glued_digit_stays_in_the_code():
    first = detect_part_mark("ABC-1231", "ABC-1231")
    second = detect_part_mark("ABC-1232", "ABC-1232")
    assert first is None
    assert second is None


def test_single_part_file_is_left_unchanged(tmp_path):
    folder = tmp_path / "inbox"
    folder.mkdir()
    parsed, plans = _plans_for(["START-628a.mp4"], str(folder))
    assert parsed[0].part_index is None
    assert "（Part" not in os.path.basename(plans[0].target_path)
