"""框选文件和文件夹后，能收成同一批视频。"""

import os
import time

import pytest

FOLDER = os.environ.get("MEDIA_SCRAPER_SAMPLE_DIR", "")


def _app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _wait_rows(model, directory: str, timeout: float = 8.0) -> int:
    from PySide6.QtWidgets import QApplication

    index = model.index(directory)
    deadline = time.time() + timeout
    while time.time() < deadline:
        QApplication.processEvents()
        count = model.rowCount(index)
        if count > 0:
            return count
        time.sleep(0.02)
    return model.rowCount(index)


@pytest.mark.skipif(
    not FOLDER or not os.path.isdir(FOLDER) or len(os.listdir(FOLDER)) < 11,
    reason="设置 MEDIA_SCRAPER_SAMPLE_DIR 指向本地样本目录后才运行",
)
def test_box_select_jab_folder_collects_every_video():
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from core.orchestrator import Pipeline
    from core.scanner import collect_videos
    from gui.selection_dialog import SelectionDialog

    app = _app()
    dialog = SelectionDialog(FOLDER)
    dialog.resize(640, 480)
    dialog.show()
    app.processEvents()
    rows = _wait_rows(dialog._model, FOLDER)
    dialog._view.setRootIndex(dialog._model.index(FOLDER))
    app.processEvents()
    assert rows >= 11

    viewport = dialog._view.viewport()
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(4, 4))
    QTest.mouseMove(viewport, QPoint(max(viewport.width() - 4, 8), max(viewport.height() - 4, 8)))
    QTest.mouseRelease(
        viewport,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QPoint(max(viewport.width() - 4, 8), max(viewport.height() - 4, 8)),
    )
    app.processEvents()
    selected = dialog.selected_paths()
    dialog.close()

    assert len(selected) == rows
    assert any(os.path.isdir(path) for path in selected)
    assert any(path.lower().endswith(".mp4") for path in selected)

    videos = collect_videos(selected)
    names = [os.path.basename(path) for path in videos]
    assert len(videos) == 16
    assert all(name.lower().endswith(".mp4") for name in names)
    assert not any(name.lower().endswith((".srt", ".torrent", ".ini")) for name in names)
    assert "RCTD-706.mp4" in names
    assert any(name.startswith("091926-001") for name in names)

    plans = Pipeline({"scan": {}, "rename": {}, "metadata": {}}).run_dry(selected)
    assert len(plans) == 16
