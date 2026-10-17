# -*- coding: utf-8 -*-
"""全局热键：低级键盘钩子（WH_KEYBOARD_LL）+ 演示模式锁字母键。"""

from __future__ import annotations

import ctypes
import queue
import threading
from ctypes import wintypes
from typing import Callable, Dict, Optional, Tuple

from .win32_input import (IS_WINDOWS, ULONG_PTR, kernel32, name_to_vk, user32,
                          vk_to_label)

WH_KEYBOARD_LL = 13
HC_ACTION = 0
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
WM_QUIT = 0x0012
LLKHF_INJECTED = 0x10
LLKHF_LOWER_IL_INJECTED = 0x02
VK_ESCAPE = 0x1B
VK_LETTERS = frozenset(range(0x41, 0x5B))

MODIFIER_VKS = {"none": (), "ctrl": (0x11,), "alt": (0x12,),
                "shift": (0x10,), "win": (0x5B, 0x5C)}


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM,
                              wintypes.LPARAM)


class GlobalHotkeys:
    def __init__(self, bindings: Dict[str, str], require_modifier: str = "none",
                 swallow: bool = True, gate: Optional[Callable[[], bool]] = None,
                 swallow_gate: Optional[Callable[[], bool]] = None,
                 on_action: Optional[Callable[[str], None]] = None,
                 on_unlock: Optional[Callable[[str], None]] = None,
                 lock_hold_key: str = "esc", lock_hold_seconds: float = 2.0,
                 logger: Optional[Callable[[str], None]] = None):
        self.log = logger or (lambda message: None)
        self.on_action = on_action or (lambda name: None)
        self._on_unlock = on_unlock or (lambda reason: None)
        self._swallow = bool(swallow)
        self._require = (require_modifier or "none").lower()
        self._gate = gate
        self._swallow_gate = swallow_gate
        self._bindings: Dict[int, str] = {}
        self._labels: Dict[str, str] = {}
        self._thread: Optional[threading.Thread] = None
        self._dispatcher: Optional[threading.Thread] = None
        self._hook = 0
        self._thread_id = 0
        self._ready = threading.Event()
        self._stop_dispatcher = threading.Event()
        self._queue: "queue.Queue[str]" = queue.Queue(maxsize=64)
        self._pressed: set = set()
        self._error: Optional[str] = None
        self._proc_ref = None
        self._lock_enabled = False
        self._lock_hold_vk = name_to_vk(lock_hold_key or "esc") or VK_ESCAPE
        self._lock_hold_seconds = max(0.5, float(lock_hold_seconds or 2.0))
        self._hold_timer: Optional[threading.Timer] = None
        self.set_bindings(bindings)

    # ---------------- 配置 ----------------
    def set_bindings(self, bindings: Dict[str, str]) -> None:
        table: Dict[int, str] = {}
        labels: Dict[str, str] = {}
        for action, key_name in (bindings or {}).items():
            vk = name_to_vk(key_name or "")
            if vk:
                table[vk] = action
            labels[action] = vk_to_label(vk) if vk else "（未设置）"
        self._bindings = table
        self._labels = labels

    def set_swallow(self, swallow: bool) -> None:
        self._swallow = bool(swallow)

    def set_swallow_gate(self, gate: Optional[Callable[[], bool]]) -> None:
        self._swallow_gate = gate

    def set_lock_hold(self, key_name: str, seconds: float) -> None:
        self._lock_hold_vk = name_to_vk(key_name or "esc") or VK_ESCAPE
        self._lock_hold_seconds = max(0.5, float(seconds or 2.0))

    def label_for(self, action: str) -> str:
        return self._labels.get(action, "（未设置）")

    def describe(self) -> str:
        names = {"start": "开始/继续", "stop": "停止", "panic": "紧急停止"}
        parts = ["%s=%s" % (names[a], self._labels[a])
                 for a in ("start", "stop", "panic") if a in self._labels]
        return "  ".join(parts) if parts else "（未绑定热键）"

    # ---------------- 演示模式 ----------------
    @property
    def key_locked(self) -> bool:
        return bool(self._lock_enabled)

    def set_key_lock(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._lock_enabled:
            return
        self._lock_enabled = enabled
        self._cancel_hold_timer()
        self._pressed.clear()
        if enabled:
            self.log("演示模式已开启（字母键被锁住，按住 %s 可解锁）"
                     % vk_to_label(self._lock_hold_vk))
        else:
            self.log("演示模式已关闭（字母键已恢复正常）")

    def _start_hold_timer(self) -> None:
        self._cancel_hold_timer()
        timer = threading.Timer(self._lock_hold_seconds, self._hold_fired)
        timer.daemon = True
        self._hold_timer = timer
        timer.start()

    def _cancel_hold_timer(self) -> None:
        timer = self._hold_timer
        self._hold_timer = None
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass

    def _hold_fired(self) -> None:
        if not self._lock_enabled:
            return
        self._lock_enabled = False
        self._pressed.clear()
        try:
            self._on_unlock("按住 %s" % vk_to_label(self._lock_hold_vk))
        except Exception:
            pass

    # ---------------- 生命周期 ----------------
    def start(self, timeout: float = 4.0) -> Tuple[bool, str]:
        if not IS_WINDOWS:
            return False, "全局热键仅支持 Windows"
        if self._thread and self._thread.is_alive():
            return True, "已在运行"
        if not self._bindings:
            return False, "没有可用的热键绑定"
        self._error = None
        self._ready.clear()
        self._stop_dispatcher.clear()
        self._pressed.clear()
        self._thread = threading.Thread(target=self._run_hook, name="autotyper-hook", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        if self._error:
            return False, self._error
        if not self._hook:
            return False, "键盘钩子安装失败"
        if not (self._dispatcher and self._dispatcher.is_alive()):
            self._dispatcher = threading.Thread(target=self._dispatch_loop,
                                                name="autotyper-hotkey-dispatch", daemon=True)
            self._dispatcher.start()
        return True, "热键已生效：%s" % self.describe()

    def stop(self) -> None:
        self.set_key_lock(False)
        self._stop_dispatcher.set()
        if self._thread_id:
            try:
                user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=2.0)
        self._thread = None
        self._hook = 0

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self._hook)

    def _run_hook(self) -> None:
        try:
            self._thread_id = int(kernel32.GetCurrentThreadId())
            self._proc_ref = HOOKPROC(self._callback)
            module = kernel32.GetModuleHandleW(None)
            user32.SetWindowsHookExW.restype = wintypes.HANDLE
            user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC,
                                                 wintypes.HINSTANCE, wintypes.DWORD]
            user32.UnhookWindowsHookEx.restype = wintypes.BOOL
            user32.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
            user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int,
                                              wintypes.WPARAM, wintypes.LPARAM]
            user32.CallNextHookEx.restype = ctypes.c_ssize_t
            self._hook = int(user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc_ref, module, 0) or 0)
            if not self._hook:
                self._error = "安装全局键盘钩子失败：%s" % ctypes.WinError(ctypes.get_last_error())
                self._ready.set()
                return
            message = wintypes.MSG()
            user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
            self._ready.set()
            while True:
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result in (0, -1):
                    break
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception as exc:  # pragma: no cover
            self._error = "键盘钩子线程异常：%s" % exc
            self._ready.set()
        finally:
            self._lock_enabled = False
            self._cancel_hold_timer()
            if self._hook:
                try:
                    user32.UnhookWindowsHookEx(self._hook)
                except Exception:
                    pass
            self._hook = 0

    def _should_swallow(self) -> bool:
        if not self._swallow:
            return False
        gate = self._swallow_gate
        if gate is None:
            return True
        try:
            return bool(gate())
        except Exception:
            return True

    def _pass(self, n_code, w_param, l_param):
        try:
            return user32.CallNextHookEx(0, n_code, w_param, l_param)
        except Exception:
            return 0

    def _callback(self, n_code, w_param, l_param):
        try:
            if n_code != HC_ACTION:
                return self._pass(n_code, w_param, l_param)
            info = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if info.flags & (LLKHF_INJECTED | LLKHF_LOWER_IL_INJECTED):
                return self._pass(n_code, w_param, l_param)
            vk = int(info.vkCode)
            is_down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
            is_up = w_param in (WM_KEYUP, WM_SYSKEYUP)

            if self._lock_enabled:
                if vk == self._lock_hold_vk:
                    if is_down and vk not in self._pressed:
                        self._pressed.add(vk)
                        self._start_hold_timer()
                    elif is_up:
                        self._cancel_hold_timer()
                        self._pressed.discard(vk)
                    return self._pass(n_code, w_param, l_param)
                if vk in VK_LETTERS:
                    return 1

            action = self._match(vk)
            if action is None:
                return self._pass(n_code, w_param, l_param)
            if is_down and vk not in self._pressed:
                self._pressed.add(vk)
                try:
                    self._queue.put_nowait(action)
                except queue.Full:
                    pass
            elif is_up:
                self._pressed.discard(vk)
            if self._should_swallow() and (is_down or is_up):
                return 1
            return self._pass(n_code, w_param, l_param)
        except Exception:
            return self._pass(n_code, w_param, l_param)

    def _match(self, vk: int) -> Optional[str]:
        action = self._bindings.get(vk)
        if action is None:
            return None
        if self._gate is not None:
            try:
                if not self._gate():
                    return None
            except Exception:
                return None
        for modifier in MODIFIER_VKS.get(self._require, ()):
            if not (int(user32.GetAsyncKeyState(modifier)) & 0x8000):
                return None
        return action

    def _dispatch_loop(self) -> None:
        while not self._stop_dispatcher.is_set():
            try:
                action = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self.on_action(action)
            except Exception as exc:
                self.log("热键处理出错（%s）：%s" % (action, exc))
