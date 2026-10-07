"""超长文件名确认窗口：建议名可直接用，超长输入不能通过。"""

import os

from PySide6.QtWidgets import QApplication, QDialog, QLabel

from core.models import Metadata, ParsedFilename, VideoType
from gui.long_name_dialog import LongNameDialog
from rename.renamer import build_rename_plan


def _app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _plan(tmp_path):
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
    return build_rename_plan(
        parsed,
        templates={"coded": "{code} {title}", "fallback": "{title}"},
        metadata=Metadata(title=title, code="CAWB-041"),
        create_subfolder=True,
        output_mode="custom_dir",
        custom_output_dir=str(out_dir),
    )


def test_dialog_rejects_overlong_name_and_accepts_custom(tmp_path):
    _app()
    plan = _plan(tmp_path)
    dialog = LongNameDialog([plan])
    suggested = os.path.splitext(os.path.basename(plan.target_path))[0]
    assert dialog._rows[0].edit.text() == suggested
    labels = [widget.text() for widget in dialog._rows[0].findChildren(QLabel)]
    assert any(plan.full_stem in text for text in labels)

    dialog._rows[0].edit.setText("無" * 200)
    dialog._accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "字节" in dialog._error.text()

    dialog._rows[0].edit.setText("CAWB-041 自定标题.mp4")
    dialog._accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.choices() == {plan.source_path: "CAWB-041 自定标题"}
