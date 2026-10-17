# -*- coding: utf-8 -*-
"""系统托盘图标（Shell_NotifyIconW + 隐藏消息窗口）。"""

from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes
from typing import Callable, List, Optional

from . import paths
from .win32_input import IS_WINDOWS, ULONG_PTR, kernel32, shell32, user32

WM_APP = 0x8000
WM_SHOW_SETTINGS = WM_APP + 5
WM_QUIT_APP = WM_APP + 6
WM_TRAY = WM_APP + 7
WM_COMMAND = 0x0111
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_RBUTTONUP = 0x0205
WM_LBUTTONUP = 0x0202
WM_CONTEXTMENU = 0x007B
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040
MF_STRING, MF_SEPARATOR, MF_CHECKED, MF_UNCHECKED = 0x0000, 0x0800, 0x0008, 0x0000
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100


class MenuItem:
    def __init__(self, ident: str, label: str, checked: Optional[bool] = None,
                 separator: bool = False):
        self.ident = ident
        self.label = label
        self.checked = checked
        self.separator = separator


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HANDLE),
                ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD),
                ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
                ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HANDLE),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HANDLE),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class TrayIcon:
    CLASS_NAME = os.environ.get("AUTOTYPER_TRAY_CLASS") or "AutoTyperStandaloneTrayWnd"
    TITLE = "打字助手"

    def __init__(self, tooltip: str = "打字助手",
                 menu_provider: Optional[Callable[[], List[MenuItem]]] = None,
                 on_command: Optional[Callable[[str], None]] = None,
                 on_show: Optional[Callable[[], None]] = None,
                 on_quit: Optional[Callable[[], None]] = None):
        self.tooltip = (tooltip or "打字助手")[:120]
        self.menu_provider = menu_provider or (lambda: [])
        self.on_command = on_command or (lambda ident: None)
        self.on_show = on_show or (lambda: None)
        self.on_quit = on_quit or (lambda: None)
        self.hwnd = 0
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._failed: Optional[str] = None
        self._running = False
        self._proc_ref = None
        self._icon = 0
        self._menu_items: List[MenuItem] = []

    # ---------------- 生命周期 ----------------
    def start(self, timeout: float = 5.0, with_icon: bool = True) -> bool:
        if not IS_WINDOWS:
            self._failed = "仅支持 Windows"
            return False
        if self._thread and self._thread.is_alive():
            return self._running
        self._ready.clear()
        self._failed = None
        self._thread = threading.Thread(target=self._run, args=(with_icon,),
                                        name="autotyper-tray", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        if not self._running and not self._failed:
            self._failed = "托盘窗口创建超时"
        return self._running

    def stop(self) -> None:
        if not self.hwnd:
            return
        try:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
        except Exception:
            pass
        if self._thread:
            self._thread.join(timeout=2.0)
        self._thread = None
        self._running = False
        self.hwnd = 0

    @property
    def running(self) -> bool:
        return self._running

    @property
    def error(self) -> Optional[str]:
        return self._failed

    # ---------------- 提示/气泡 ----------------
    def update_tooltip(self, text: str) -> None:
        self.tooltip = (text or "打字助手")[:120]
        if self.hwnd:
            try:
                data = self._make_data()
                data.uFlags = NIF_TIP
                shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data))
            except Exception:
                pass

    def notify(self, title: str, message: str) -> None:
        if not self.hwnd:
            return
        try:
            data = self._make_data()
            data.uFlags = NIF_INFO
            data.szInfo = (message or "")[:250]
            data.szInfoTitle = (title or "打字助手")[:60]
            data.dwInfoFlags = 0x00000001
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data))
        except Exception:
            pass

    # ---------------- 内部 ----------------
    def _load_icon(self) -> int:
        try:
            path = paths.ICON_PATH
            if os.path.exists(path):
                handle = user32.LoadImageW(None, path, IMAGE_ICON, 0, 0,
                                           LR_LOADFROMFILE | LR_DEFAULTSIZE)
                if handle:
                    return int(handle)
        except Exception:
            pass
        try:
            return int(user32.LoadIconW(None, ctypes.c_wchar_p(32512)) or 0)   # IDI_APPLICATION
        except Exception:
            return 0

    def _make_data(self) -> NOTIFYICONDATAW:
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self.hwnd
        data.uID = 1
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        data.uCallbackMessage = WM_TRAY
        data.hIcon = self._icon
        data.szTip = self.tooltip
        return data

    def _run(self, with_icon: bool = True) -> None:
        try:
            kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
            kernel32.GetModuleHandleW.restype = wintypes.HMODULE
            instance = kernel32.GetModuleHandleW(None)
            self._proc_ref = WNDPROC(self._wndproc)
            wc = WNDCLASSW()
            wc.style = 0
            wc.lpfnWndProc = ctypes.cast(self._proc_ref, ctypes.c_void_p)
            wc.hInstance = instance
            wc.lpszClassName = self.CLASS_NAME
            user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
            user32.RegisterClassW.restype = wintypes.ATOM
            user32.RegisterClassW(ctypes.byref(wc))
            user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR,
                                               wintypes.LPCWSTR, wintypes.DWORD,
                                               ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                               ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                               wintypes.HINSTANCE, wintypes.LPVOID]
            user32.CreateWindowExW.restype = wintypes.HWND
            user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
            user32.RegisterClassW.restype = wintypes.ATOM
            # WS_EX_TOOLWINDOW：这个只用来收消息的隐藏窗口绝不能出现在任务栏
            hwnd = int(user32.CreateWindowExW(0x00000080, self.CLASS_NAME, self.TITLE, 0, 0, 0, 0, 0,
                                              None, None, instance, None) or 0)
            self.hwnd = hwnd
            if not hwnd:
                self._failed = "创建托盘窗口失败：%s" % ctypes.WinError(ctypes.get_last_error())
                self._ready.set()
                return
            shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
            shell32.Shell_NotifyIconW.restype = wintypes.BOOL
            if with_icon:
                self._icon = self._load_icon()
                ok = shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._make_data()))
                if not ok:
                    code = ctypes.get_last_error()
                    self._running = False
                    self._failed = "添加托盘图标失败（Win32 %s）" % code
                    self._ready.set()
                else:
                    self._running = True
                    self._ready.set()
            else:
                self._running = True
                self._ready.set()
            message = wintypes.MSG()
            while True:
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result in (0, -1):
                    break
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception as exc:  # pragma: no cover
            self._failed = "托盘线程异常：%s" % exc
            self._ready.set()
        finally:
            try:
                if self._icon:
                    data = NOTIFYICONDATAW()
                    data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
                    data.hWnd = self.hwnd
                    data.uID = 1
                    shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(data))
            except Exception:
                pass
            self._running = False

    def _wndproc(self, hwnd, msg, w_param, l_param):
        try:
            if msg == WM_TRAY:
                event = int(l_param) & 0xFFFF
                if event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    self._show_menu()
                elif event == WM_LBUTTONUP:
                    self.on_show()
                return 0
            if msg == WM_SHOW_SETTINGS:
                self.on_show()
                return 0
            if msg == WM_QUIT_APP:
                self.on_quit()
                return 0
            if msg == WM_COMMAND:
                ident = int(w_param) & 0xFFFF
                for item in self._menu_items:
                    if item.command_id == ident:
                        try:
                            self.on_command(item.ident)
                        except Exception:
                            pass
                        break
                return 0
            if msg == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception:
            pass
        return int(user32.DefWindowProcW(hwnd, msg, w_param, l_param))

    def _show_menu(self) -> None:
        try:
            items = list(self.menu_provider() or [])
            self._menu_items = items
            menu = user32.CreatePopupMenu()
            for index, item in enumerate(items):
                if item.separator or item.label == "-":
                    user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                    continue
                flags = MF_STRING
                if item.checked is True:
                    flags |= MF_CHECKED
                user32.AppendMenuW(menu, flags, 100 + index, item.label)
                item.command_id = 100 + index
            point = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(point))
            user32.SetForegroundWindow(self.hwnd)
            choice = int(user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD,
                                               point.x, point.y, 0, self.hwnd, None) or 0)
            user32.PostMessageW(self.hwnd, 0x0000, 0, 0)
            user32.DestroyMenu(menu)
            if choice:
                for item in items:
                    if getattr(item, "command_id", -1) == choice:
                        self.on_command(item.ident)
                        break
        except Exception:
            pass
