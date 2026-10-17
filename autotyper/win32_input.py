# -*- coding: utf-8 -*-
"""Windows 底层输入/窗口/进程工具（只用标准库 ctypes）。

SendInput + KEYEVENTF_UNICODE 绕开输入法直接送字符；
另提供“按键式”输入（VK + Shift）：大小写、符号按真实键盘语义，
保证大写字母一定打出大写，也兼容只认普通按键的软件。
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from typing import Optional, Tuple

IS_WINDOWS = sys.platform.startswith("win")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True) if IS_WINDOWS else None
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True) if IS_WINDOWS else None

ULONG_PTR = wintypes.WPARAM

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_CAPITAL = 0x14
VK_RETURN = 0x0D
VK_TAB = 0x09
VK_SPACE = 0x20
VK_BACK = 0x08
VK_ESCAPE = 0x1B
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_HOME = 0x24
VK_END = 0x23
VK_PRIOR = 0x21
VK_NEXT = 0x22
VK_INSERT = 0x2D
VK_DELETE = 0x2E
VK_LWIN = 0x5B
VK_RWIN = 0x5C

GA_ROOT = 2
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
SW_RESTORE = 9


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def _bind() -> None:
    if not IS_WINDOWS:
        return
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    user32.SetWindowLongW.restype = ctypes.c_long
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetKeyState.argtypes = [ctypes.c_int]
    user32.GetKeyState.restype = ctypes.c_short
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    if advapi32 is not None:
        advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                              ctypes.POINTER(wintypes.HANDLE)]
        advapi32.OpenProcessToken.restype = wintypes.BOOL
        advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                 ctypes.c_void_p, wintypes.DWORD,
                                                 ctypes.POINTER(wintypes.DWORD)]
        advapi32.GetTokenInformation.restype = wintypes.BOOL
        advapi32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
        advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
        advapi32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
    user32.VkKeyScanW.argtypes = [ctypes.c_wchar]
    user32.VkKeyScanW.restype = ctypes.c_short
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    # ---- 窗口/菜单/消息 ----
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = wintypes.BOOL
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.PostQuitMessage.restype = None
    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT,
                                          wintypes.WPARAM, wintypes.LPARAM]
    user32.PostThreadMessageW.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                   wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = ctypes.c_int
    user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                    wintypes.UINT, wintypes.UINT, wintypes.UINT]
    user32.PeekMessageW.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.TranslateMessage.restype = wintypes.BOOL
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = ctypes.c_ssize_t
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                  ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
    user32.LoadIconW.restype = wintypes.HANDLE
    user32.CreatePopupMenu.argtypes = []
    user32.CreatePopupMenu.restype = wintypes.HMENU
    user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ULONG_PTR, wintypes.LPCWSTR]
    user32.AppendMenuW.restype = wintypes.BOOL
    user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                      ctypes.c_void_p]
    user32.TrackPopupMenu.restype = ctypes.c_int
    user32.DestroyMenu.argtypes = [wintypes.HMENU]
    user32.DestroyMenu.restype = wintypes.BOOL
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.GetKeyState.argtypes = [ctypes.c_int]
    user32.GetKeyState.restype = ctypes.c_short


_bind()


def selftest_structs() -> dict:
    try:
        size = ctypes.sizeof(INPUT)
        pointer = ctypes.sizeof(ctypes.c_void_p)
        expected = 40 if pointer == 8 else 28
        offsets_ok = (INPUT.ki.offset == (8 if pointer == 8 else 4)
                      and KEYBDINPUT.wVk.offset == 0 and KEYBDINPUT.wScan.offset == 2
                      and KEYBDINPUT.dwFlags.offset == 4)
        return {"ok": size == expected and offsets_ok, "sizeof_INPUT": size,
                "expected": expected, "sizeof_KEYBDINPUT": ctypes.sizeof(KEYBDINPUT),
                "pointer": pointer}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


_NAME_TO_VK = {
    "down": VK_DOWN, "up": VK_UP, "left": VK_LEFT, "right": VK_RIGHT,
    "esc": VK_ESCAPE, "escape": VK_ESCAPE, "enter": VK_RETURN, "return": VK_RETURN,
    "space": VK_SPACE, "tab": VK_TAB, "backspace": VK_BACK, "back": VK_BACK,
    "home": VK_HOME, "end": VK_END, "pageup": VK_PRIOR, "pagedown": VK_NEXT,
    "insert": VK_INSERT, "delete": VK_DELETE, "del": VK_DELETE,
    "shift": VK_SHIFT, "ctrl": VK_CONTROL, "control": VK_CONTROL, "alt": VK_MENU,
    "capslock": VK_CAPITAL, "win": VK_LWIN,
}
for _i in range(1, 25):
    _NAME_TO_VK["f%d" % _i] = 0x6F + _i
for _c in "abcdefghijklmnopqrstuvwxyz":
    _NAME_TO_VK[_c] = ord(_c.upper())
for _d in "0123456789":
    _NAME_TO_VK[_d] = ord(_d)
_NAME_TO_VK.update({",": 0xBC, ".": 0xBE, "/": 0xBF, ";": 0xBA, "'": 0xDE,
                    "[": 0xDB, "]": 0xDD, "\\": 0xDC, "-": 0xBD, "=": 0xBB})

_VK_TO_LABEL = {
    VK_DOWN: "↓", VK_UP: "↑", VK_LEFT: "←", VK_RIGHT: "→",
    VK_ESCAPE: "Esc", VK_RETURN: "Enter", VK_SPACE: "空格", VK_TAB: "Tab",
    VK_BACK: "Backspace", VK_HOME: "Home", VK_END: "End", VK_PRIOR: "PageUp",
    VK_NEXT: "PageDown", VK_INSERT: "Insert", VK_DELETE: "Delete",
    VK_SHIFT: "Shift", VK_CONTROL: "Ctrl", VK_MENU: "Alt", VK_CAPITAL: "CapsLock",
}
for _i in range(1, 25):
    _VK_TO_LABEL[0x6F + _i] = "F%d" % _i


def name_to_vk(name: str) -> int:
    if not name:
        return 0
    key = str(name).strip().lower()
    if key in _NAME_TO_VK:
        return _NAME_TO_VK[key]
    if len(key) == 1:
        return ord(key.upper()) if key.isalpha() else ord(key)
    return 0


def vk_to_label(vk: int) -> str:
    if not vk:
        return "（未设置）"
    if vk in _VK_TO_LABEL:
        return _VK_TO_LABEL[vk]
    if 0x41 <= vk <= 0x5A:
        return chr(vk)
    if 0x30 <= vk <= 0x39:
        return chr(vk)
    return "VK_0x%02X" % vk


#: 修饰键：名字 -> (各侧虚拟键码, 哪些需要 EXTENDEDKEY 标记)
MODIFIER_KEYS = (
    ("Shift", (0xA0, 0xA1), ()),
    ("Ctrl", (0xA2, 0xA3), (0xA3,)),
    ("Alt", (0xA4, 0xA5), (0xA5,)),
    ("Win", (0x5B, 0x5C), (0x5B, 0x5C)),
)


def _key_down(vk: int) -> bool:
    """这个键当前是否被按着（读系统异步键状态）。"""
    try:
        return bool(int(user32.GetAsyncKeyState(vk)) & 0x8000)
    except Exception:
        return False


def pending_modifiers(key_down=None) -> list:
    """返回当前还按着的修饰键名字（Shift/Ctrl/Alt/Win）。可注入检测函数便于测试。"""
    probe = key_down or _key_down
    found = []
    for name, vks, _extended in MODIFIER_KEYS:
        if any(probe(vk) for vk in vks):
            found.append(name)
    return found


def release_modifiers(extra_info: int = 0, key_down=None, sender=None) -> list:
    """把还按着的修饰键全部松开，返回实际释放了哪些（用于日志/提示）。

    为什么要做：用户按 ↓ 时如果手还压着 Shift/Ctrl，之后注入的按键会和它
    组合成快捷键，导致打出大写错乱、触发 IDE 快捷键等。
    """
    probe = key_down or _key_down
    released = pending_modifiers(probe)
    if not released:
        return []
    items = []
    for name, vks, extended_vks in MODIFIER_KEYS:
        if name not in released:
            continue
        for vk in vks:
            if not probe(vk):
                continue
            flags = KEYEVENTF_KEYUP
            if vk in extended_vks:
                flags |= KEYEVENTF_EXTENDEDKEY
            items.append(_key_input(vk, 0, flags, extra_info))
    if items:
        if sender is not None:
            sender(items)
        else:
            _send(items)
    return released


def _key_input(vk: int, scan: int, flags: int, extra_info: int = 0) -> INPUT:
    item = INPUT()
    item.type = INPUT_KEYBOARD
    item.ki = KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0,
                         dwExtraInfo=ULONG_PTR(extra_info))
    return item


def _send(items) -> int:
    if not IS_WINDOWS or not items:
        return 0
    array = (INPUT * len(items))(*items)
    return int(user32.SendInput(len(items), array, ctypes.sizeof(INPUT)))


def send_key(vk: int, extra_info: int = 0, up: Optional[bool] = None,
             extended: Optional[bool] = None) -> int:
    """按一次键。

    up=None  → 完整按一次（先按下再抬起，共两个事件）
    up=False → 只发“按下”
    up=True  → 只发“抬起”
    """
    if not vk:
        return 0
    if extended is None:
        extended = vk in (VK_LEFT, VK_RIGHT, VK_UP, VK_DOWN, VK_HOME, VK_END,
                          VK_PRIOR, VK_NEXT, VK_INSERT, VK_DELETE, VK_LWIN, VK_RWIN,
                          0x2C, 0x91)
    base = KEYEVENTF_EXTENDEDKEY if extended else 0
    if up is None:
        return _send([_key_input(vk, 0, base, extra_info),
                      _key_input(vk, 0, base | KEYEVENTF_KEYUP, extra_info)])
    flags = base | (KEYEVENTF_KEYUP if up else 0)
    return _send([_key_input(vk, 0, flags, extra_info)])


def send_key_name(name: str, extra_info: int = 0) -> int:
    vk = name_to_vk(name)
    if not vk:
        return 0
    return send_key(vk, extra_info)


def send_text(text: str, extra_info: int = 0) -> int:
    """Unicode 注入：任何字符（含中文/emoji）都原样送达，不受输入法影响。"""
    if not text:
        return 0
    items = []
    for ch in text:
        code = ord(ch)
        if code > 0xFFFF:
            code -= 0x10000
            units = [0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF)]
        else:
            units = [code]
        for unit in units:
            items.append(_key_input(0, unit, KEYEVENTF_UNICODE, extra_info))
            items.append(_key_input(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, extra_info))
    return _send(items)


def send_backspaces(count: int, extra_info: int = 0) -> int:
    items = []
    for _ in range(max(0, int(count))):
        items.append(_key_input(VK_BACK, 0, 0, extra_info))
        items.append(_key_input(VK_BACK, 0, KEYEVENTF_KEYUP, extra_info))
    return _send(items)


def _char_to_vk(ch: str) -> Optional[Tuple[int, bool]]:
    """返回 (虚拟键码, 是否需要 Shift)，并自动考虑 CapsLock 状态。"""
    if not ch:
        return None
    try:
        scan = int(user32.VkKeyScanW(ch))
    except Exception:
        return None
    if scan == -1:
        return None
    vk = scan & 0xFF
    modifiers = (scan >> 8) & 0xFF
    if modifiers & 0x06:
        return None
    need_shift = bool(modifiers & 0x01)
    if ch.isascii() and ch.isalpha():
        caps = bool(int(user32.GetKeyState(VK_CAPITAL)) & 0x0001)
        if caps:
            need_shift = not need_shift
    return vk, need_shift


def send_char_keyed(ch: str, extra_info: int = 0) -> int:
    """按键式输入单个字符：大写字母自动加 Shift，符号按真实键盘语义。"""
    if ch == "\n":
        return send_key_name("enter", extra_info)
    if ch == "\t":
        return send_key_name("tab", extra_info)
    mapped = _char_to_vk(ch)
    if mapped is None:
        return send_text(ch, extra_info)
    vk, need_shift = mapped
    items = []
    if need_shift:
        items.append(_key_input(VK_SHIFT, 0, 0, extra_info))
    items.append(_key_input(vk, 0, 0, extra_info))
    items.append(_key_input(vk, 0, KEYEVENTF_KEYUP, extra_info))
    if need_shift:
        items.append(_key_input(VK_SHIFT, 0, KEYEVENTF_KEYUP, extra_info))
    return _send(items)


def send_text_keyed(text: str, extra_info: int = 0) -> int:
    """ASCII 用真实按键（自动处理大小写），中文等仍用 Unicode 注入。"""
    count = 0
    for ch in text:
        count += send_char_keyed(ch, extra_info)
    return count


def get_foreground_window() -> int:
    try:
        return int(user32.GetForegroundWindow() or 0)
    except Exception:
        return 0


def get_ex_style(hwnd: int) -> int:
    try:
        return int(user32.GetWindowLongW(hwnd, GWL_EXSTYLE)) & 0xFFFFFFFF
    except Exception:
        return 0


def _set_ex_style(hwnd: int, style: int) -> bool:
    try:
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_uint]
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x27)
        return True
    except Exception:
        return False


def hide_from_taskbar(hwnd: int) -> bool:
    style = get_ex_style(hwnd)
    return _set_ex_style(hwnd, (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)


def show_in_taskbar(hwnd: int) -> bool:
    style = get_ex_style(hwnd)
    return _set_ex_style(hwnd, (style | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW)


def toplevel_hwnd(child_hwnd: int) -> int:
    try:
        root = int(user32.GetAncestor(child_hwnd, GA_ROOT) or 0)
        return root or int(child_hwnd)
    except Exception:
        return int(child_hwnd)


def try_take_foreground(hwnd: int) -> bool:
    if not hwnd:
        return False
    try:
        if int(user32.IsWindowVisible(hwnd)):
            user32.ShowWindow(hwnd, SW_RESTORE)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        return get_foreground_window() == hwnd
    except Exception:
        return False


def _process_name(pid: int) -> str:
    if not pid:
        return ""
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return os.path.basename(buffer.value)
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _window_title(hwnd: int) -> str:
    try:
        length = int(user32.GetWindowTextLengthW(hwnd))
        buffer = ctypes.create_unicode_buffer(max(length + 2, 8))
        user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return buffer.value
    except Exception:
        return ""


def foreground_info() -> dict:
    hwnd = get_foreground_window()
    pid = 0
    if hwnd:
        try:
            value = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(value))
            pid = int(value.value)
        except Exception:
            pid = 0
    return {"hwnd": hwnd, "pid": pid, "process": _process_name(pid),
            "title": _window_title(hwnd)}


def foreground_signature() -> Tuple[int, int]:
    info = foreground_info()
    return (int(info.get("hwnd") or 0), int(info.get("pid") or 0))


def integrity_level() -> str:
    """当前进程完整性级别：Low / Medium / High（托盘图标需要 Medium 及以上）。"""
    if not IS_WINDOWS or advapi32 is None:
        return "Unknown"
    handle = wintypes.HANDLE()
    try:
        if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008,
                                         ctypes.byref(handle)):
            return "Unknown"
        size = wintypes.DWORD(0)
        advapi32.GetTokenInformation(handle, 25, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(max(int(size.value), 32))
        if not advapi32.GetTokenInformation(handle, 25, ctypes.cast(buffer, ctypes.c_void_p),
                                            size, ctypes.byref(size)):
            return "Unknown"
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p)).contents.value
        if not sid:
            return "Unknown"
        count_ptr = advapi32.GetSidSubAuthorityCount(sid)
        if not count_ptr:
            return "Unknown"
        count = int(count_ptr.contents.value)
        if count <= 0:
            return "Unknown"
        rid_ptr = advapi32.GetSidSubAuthority(sid, count - 1)
        if not rid_ptr:
            return "Unknown"
        rid = int(rid_ptr.contents.value)
        if rid >= 0x3000:
            return "High"
        if rid >= 0x2000:
            return "Medium"
        return "Low"
    except Exception:
        return "Unknown"
    finally:
        try:
            if handle:
                kernel32.CloseHandle(handle)
        except Exception:
            pass


def enable_dpi_awareness() -> None:
    if not IS_WINDOWS:
        return
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


class SendInputSink:
    """引擎真正用的输出端：auto 模式下 ASCII 走真实按键（大小写自动正确）。"""

    name = "windows"

    def __init__(self, input_mode: str = "auto", extra_info: int = 0):
        self.input_mode = (input_mode or "auto").lower()
        self.extra_info = extra_info
        self.stats = {"typed": 0, "backspaces": 0, "keys": 0}

    def type_text(self, text: str) -> None:
        if not text:
            return
        self.stats["typed"] += len(text)
        if self.input_mode == "unicode":
            send_text(text, self.extra_info)
        else:
            send_text_keyed(text, self.extra_info)

    def press_key(self, key: str) -> None:
        self.stats["keys"] += 1
        send_key_name(key, self.extra_info)

    def backspace(self, count: int) -> None:
        count = max(0, int(count))
        self.stats["backspaces"] += count
        send_backspaces(count, self.extra_info)

    def release_modifiers(self) -> list:
        """开始打字前释放还按着的修饰键（防止组合键残留）。"""
        return release_modifiers(self.extra_info)

    def foreground_signature(self):
        return foreground_signature()

    def process_name(self) -> str:
        return str(foreground_info().get("process") or "")
