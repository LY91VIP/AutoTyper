# -*- coding: utf-8 -*-
"""真机注入验证：证明 SendInput 真的把“正确的按键事件”送进了系统输入队列。

安全设计（很重要）：
  * 测试期间，本进程自己的低级键盘钩子会把**所有注入事件一律吞掉**，
    不管带不带标记，所以按键绝不会漏进任何一个窗口；
  * 先只发一个字符做“探针”：如果连这个都观察不到，说明本环境下看不到
    自己注入的事件，那就立刻停手并如实跳过，只可能漏出一个字符。
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
PROGRAM_DIR = os.path.dirname(HERE)
if PROGRAM_DIR not in sys.path:
    sys.path.insert(0, PROGRAM_DIR)

from autotyper.hotkeys import (HC_ACTION, HOOKPROC, KBDLLHOOKSTRUCT, WH_KEYBOARD_LL,  # noqa: E402
                               WM_KEYDOWN, WM_KEYUP)
from autotyper.win32_input import (KEYEVENTF_UNICODE, kernel32, send_backspaces,  # noqa: E402
                                   send_key, send_text, user32)

TAG = 0xA1707E57
TEXT = "Hi中🙂"
WM_QUIT = 0x0012
LLKHF_INJECTED = 0x10

seen: list = []
lock = threading.Lock()
thread_id = 0
hook_handle = 0
ready = threading.Event()
swallow_all_injected = True


def _callback(n_code, w_param, l_param):
    try:
        if n_code == HC_ACTION:
            info = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            record = {
                "vk": int(info.vkCode),
                "scan": int(info.scanCode),
                "flags": int(info.flags),
                "extra": int(info.dwExtraInfo),
                "down": w_param == WM_KEYDOWN,
            }
            if record["flags"] & LLKHF_INJECTED:
                with lock:
                    seen.append(record)
                if swallow_all_injected:
                    return 1     # 测试期间的注入事件一律吞掉，保证不漏到任何窗口
    except Exception:
        pass
    return user32.CallNextHookEx(0, n_code, w_param, l_param)


def _hook_thread():
    global thread_id, hook_handle
    thread_id = int(kernel32.GetCurrentThreadId())
    proc = HOOKPROC(_callback)
    user32.SetWindowsHookExW.restype = wintypes.HANDLE
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
    user32.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
    user32.CallNextHookEx.restype = ctypes.c_ssize_t
    hook_handle = int(user32.SetWindowsHookExW(WH_KEYBOARD_LL, proc, kernel32.GetModuleHandleW(None), 0) or 0)
    message = wintypes.MSG()
    user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
    ready.set()
    while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(message))
        user32.DispatchMessageW(ctypes.byref(message))
    if hook_handle:
        user32.UnhookWindowsHookEx(hook_handle)
    globals()["_proc"] = proc


def main() -> int:
    from autotyper.win32_input import integrity_level
    thread = threading.Thread(target=_hook_thread, daemon=True)
    thread.start()
    ready.wait(5)
    if not hook_handle:
        print("[SKIP] 装不上键盘钩子，无法验证注入")
        return 0
    print("完整性级别：%s" % integrity_level())

    try:
        time.sleep(0.3)
        # 探针：只发一个字符
        send_text("a", extra_info=TAG)
        time.sleep(0.5)
        with lock:
            probe = list(seen)
        if not probe:
            print("[SKIP] 本环境下本进程看不到自己注入的按键事件（探针没有回音），")
            print("       已立即停手，未发送任何多余内容。请在普通桌面环境复测这一项。")
            return 0

        with lock:
            seen.clear()
        send_text(TEXT, extra_info=TAG)
        send_key(0x0D, extra_info=TAG)
        send_backspaces(1, extra_info=TAG)
        time.sleep(0.6)
    finally:
        if thread_id:
            user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
        thread.join(timeout=2)

    with lock:
        events = list(seen)

    expected_units = []
    payload = TEXT.encode("utf-16-le")
    for offset in range(0, len(payload), 2):
        expected_units.append(int.from_bytes(payload[offset:offset + 2], "little"))

    failures = []
    # 注意：用 KEYEVENTF_UNICODE 注入的字符，在低级键盘钩子里既不带
    # KEYEVENTF_UNICODE 标记，vkCode 也不是 0，而是 VK_PACKET(0xE7)，
    # 真正的字符码放在 scanCode 里（实测确认：vk=231 scan=72 就是字母 H）。
    VK_PACKET = 0xE7
    char_events = [e for e in events if e["vk"] == VK_PACKET]
    if len(char_events) != len(expected_units) * 2:
        failures.append("Unicode 事件数不对：期望 %d，实际 %d" % (len(expected_units) * 2, len(char_events)))
    else:
        for index, unit in enumerate(expected_units):
            down, up = char_events[index * 2], char_events[index * 2 + 1]
            if down["scan"] != unit or down["vk"] != VK_PACKET:
                failures.append("第 %d 个字符注入错误：scan=%s vk=%s" % (index, down["scan"], down["vk"]))
            if not down["down"] or up["down"]:
                failures.append("第 %d 个字符的按下/抬起不成对" % index)
            if down.get("extra") != TAG:
                failures.append("第 %d 个字符的标记丢了（注入识别会失效）" % index)
    enter = [e for e in events if e["vk"] == 0x0D]
    back = [e for e in events if e["vk"] == 0x08]
    if len(enter) != 2:
        failures.append("回车事件数不对：%d" % len(enter))
    if len(back) != 2:
        failures.append("退格事件数不对：%d" % len(back))

    print("钩子观察到的注入事件：%d 个（字符 %d + 回车 %d + 退格 %d）"
          % (len(events), len(char_events), len(enter), len(back)))
    if failures:
        print("[FAIL] 真机注入验证")
        for item in failures:
            print("       - " + item)
        return 1
    print("[PASS] 真机注入验证：Unicode 字符/回车/退格都被系统正确接收（事件已全部拦下，未进入任何窗口）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
