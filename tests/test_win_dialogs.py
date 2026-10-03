"""Windows 文件夹窗口能创建，且不弹出界面。"""

from gui.win_dialogs import probe_folder_dialog


def test_windows_folder_dialog_can_be_created():
    probe_folder_dialog()
