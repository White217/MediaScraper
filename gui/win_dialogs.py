"""调用 Windows 自己的选择窗口。文件夹可多选，视频用系统的打开文件窗口。"""

from __future__ import annotations

import ctypes
import logging
import os
import uuid
from ctypes import POINTER, byref, c_void_p, c_wchar_p, windll, wintypes

logger = logging.getLogger(__name__)

_CLSCTX_INPROC_SERVER = 1
_COINIT_APARTMENTTHREADED = 2
_FOS_PICKFOLDERS = 0x20
_FOS_FORCEFILESYSTEM = 0x40
_FOS_ALLOWMULTISELECT = 0x200
_FOS_PATHMUSTEXIST = 0x800
_SIGDN_FILESYSPATH = 0x80058000
_ERROR_CANCELLED = 0x800704C7
_RPC_E_CHANGED_MODE = 0x80010106

_CLSID_FILE_OPEN_DIALOG = "DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7"
_IID_FILE_OPEN_DIALOG = "D57C7288-D4AD-4768-BE02-9D969532D960"
_IID_SHELL_ITEM = "43826D1E-E718-42EE-BC55-A1E261C37BFE"


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(text: str) -> _GUID:
    value = _GUID()
    ctypes.memmove(byref(value), uuid.UUID(text).bytes_le, ctypes.sizeof(value))
    return value


def _method(obj, index, restype, *argtypes):
    table = ctypes.cast(obj, POINTER(POINTER(c_void_p)))[0]
    prototype = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
    return prototype(table[index])


def _release(obj) -> None:
    if obj:
        _method(obj, 2, ctypes.c_ulong)(obj)


def _ok(code: int) -> bool:
    return ctypes.c_long(code).value >= 0


def pick_folders(hwnd: int = 0, start_dir: str = "", title: str = "选择文件夹") -> list[str] | None:
    """打开 Windows 多选文件夹窗口。取消时返回 None。"""
    ole32 = windll.ole32
    shell32 = windll.shell32
    hr = ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
    co_owned = _ok(hr)
    if hr == _RPC_E_CHANGED_MODE:
        co_owned = False

    dialog = c_void_p()
    clsid = _guid(_CLSID_FILE_OPEN_DIALOG)
    iid = _guid(_IID_FILE_OPEN_DIALOG)
    ole32.CoCreateInstance.argtypes = [
        POINTER(_GUID), c_void_p, wintypes.DWORD, POINTER(_GUID), POINTER(c_void_p),
    ]
    ole32.CoCreateInstance.restype = ctypes.c_long
    created = ole32.CoCreateInstance(
        byref(clsid), None, _CLSCTX_INPROC_SERVER, byref(iid), byref(dialog),
    )
    if not _ok(created) or not dialog:
        if co_owned:
            ole32.CoUninitialize()
        raise OSError(f"无法打开 Windows 文件夹窗口: 0x{created & 0xFFFFFFFF:08X}")

    folder_item = c_void_p()
    try:
        options = _FOS_PICKFOLDERS | _FOS_ALLOWMULTISELECT | _FOS_FORCEFILESYSTEM | _FOS_PATHMUSTEXIST
        set_options = _method(dialog, 9, ctypes.c_long, wintypes.DWORD)
        if not _ok(set_options(dialog, options)):
            raise OSError("Windows 文件夹窗口不接受多选")
        set_title = _method(dialog, 17, ctypes.c_long, c_wchar_p)
        set_title(dialog, title)

        start = start_dir if start_dir and os.path.isdir(start_dir) else ""
        if start:
            shell32.SHCreateItemFromParsingName.argtypes = [
                c_wchar_p, c_void_p, POINTER(_GUID), POINTER(c_void_p),
            ]
            shell32.SHCreateItemFromParsingName.restype = ctypes.c_long
            item_iid = _guid(_IID_SHELL_ITEM)
            if _ok(shell32.SHCreateItemFromParsingName(start, None, byref(item_iid), byref(folder_item))):
                set_folder = _method(dialog, 12, ctypes.c_long, c_void_p)
                set_folder(dialog, folder_item)

        show = _method(dialog, 3, ctypes.c_long, wintypes.HWND)
        shown = show(dialog, wintypes.HWND(hwnd or 0))
        if shown & 0xFFFFFFFF == _ERROR_CANCELLED:
            return None
        if not _ok(shown):
            logger.warning("文件夹窗口未完成: 0x%08X", shown & 0xFFFFFFFF)
            return None

        results = c_void_p()
        get_results = _method(dialog, 27, ctypes.c_long, POINTER(c_void_p))
        if not _ok(get_results(dialog, byref(results))) or not results:
            return []
        try:
            count = wintypes.DWORD()
            get_count = _method(results, 7, ctypes.c_long, POINTER(wintypes.DWORD))
            get_count(results, byref(count))
            paths: list[str] = []
            get_item = _method(results, 8, ctypes.c_long, wintypes.DWORD, POINTER(c_void_p))
            for index in range(count.value):
                item = c_void_p()
                if not _ok(get_item(results, index, byref(item))) or not item:
                    continue
                name = c_wchar_p()
                get_name = _method(item, 5, ctypes.c_long, wintypes.DWORD, POINTER(c_wchar_p))
                ole32.CoTaskMemFree.argtypes = [c_void_p]
                if _ok(get_name(item, _SIGDN_FILESYSPATH, byref(name))) and name.value:
                    paths.append(os.path.normpath(name.value))
                    ole32.CoTaskMemFree(name)
                _release(item)
            return paths
        finally:
            _release(results)
    finally:
        _release(folder_item)
        _release(dialog)
        if co_owned:
            ole32.CoUninitialize()


def probe_folder_dialog() -> None:
    """创建系统文件夹窗口并立刻释放。不显示窗口。"""
    ole32 = windll.ole32
    hr = ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
    co_owned = _ok(hr)
    if hr == _RPC_E_CHANGED_MODE:
        co_owned = False
    dialog = c_void_p()
    clsid = _guid(_CLSID_FILE_OPEN_DIALOG)
    iid = _guid(_IID_FILE_OPEN_DIALOG)
    ole32.CoCreateInstance.argtypes = [
        POINTER(_GUID), c_void_p, wintypes.DWORD, POINTER(_GUID), POINTER(c_void_p),
    ]
    ole32.CoCreateInstance.restype = ctypes.c_long
    created = ole32.CoCreateInstance(
        byref(clsid), None, _CLSCTX_INPROC_SERVER, byref(iid), byref(dialog),
    )
    try:
        if not _ok(created) or not dialog:
            raise OSError(f"无法创建文件夹窗口: 0x{created & 0xFFFFFFFF:08X}")
        options = _FOS_PICKFOLDERS | _FOS_ALLOWMULTISELECT | _FOS_FORCEFILESYSTEM | _FOS_PATHMUSTEXIST
        set_options = _method(dialog, 9, ctypes.c_long, wintypes.DWORD)
        applied = set_options(dialog, options)
        if not _ok(applied):
            raise OSError(f"无法设置多选文件夹: 0x{applied & 0xFFFFFFFF:08X}")
    finally:
        _release(dialog)
        if co_owned:
            ole32.CoUninitialize()
