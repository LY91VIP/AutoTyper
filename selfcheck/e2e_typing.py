# -*- coding: utf-8 -*-
"""真实链路测试：用 SendInput 把字打进一个真实窗口，再读回来比对。

这是唯一能证明“注入的按键确实落到了目标程序里”的测试，所以它会创建一个
自己的小窗口并抢焦点。安全措施：开始打字前必须确认前台窗口就是本窗口，
否则直接放弃（什么键都不发），绝不会把内容打进用户正在用的窗口。
"""

from __future__ import annotations

import ctypes
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROGRAM_DIR = os.path.dirname(HERE)
if PROGRAM_DIR not in sys.path:
    sys.path.insert(0, PROGRAM_DIR)

import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402

from autotyper import paths  # noqa: E402
from autotyper.config import Config  # noqa: E402
from autotyper.engine import TypingEngine  # noqa: E402
from autotyper.win32_input import (SendInputSink, get_foreground_window, hide_from_taskbar,  # noqa: E402
                                   toplevel_hwnd, try_take_foreground)

TEXT = "def hello(name):\n    print('hi', name)\n"
TMP = os.path.join(PROGRAM_DIR, ".selftest-tmp")
paths.STATE_PATH = os.path.join(TMP, "state.json")


def main() -> int:
    os.makedirs(TMP, exist_ok=True)
    script = os.path.join(TMP, "e2e.txt")
    with open(script, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(TEXT)

    root = tk.Tk()
    root.title("打字助手 · 真实注入测试（会自动关闭）")
    root.geometry("520x180+80+80")
    frame = ttk.Frame(root, padding=8)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="下面应当自动出现：%r" % TEXT).pack(anchor="w")
    box = tk.Text(frame, height=5, font=("Consolas", 11))
    box.pack(fill="both", expand=True)
    box.focus_force()

    hwnd = toplevel_hwnd(root.winfo_id())
    hide_from_taskbar(hwnd)
    root.update()
    got_focus = try_take_foreground(hwnd)
    for _ in range(10):
        root.update()
        time.sleep(0.02)

    if not got_focus or get_foreground_window() != hwnd:
        print("[SKIP] 抢不到前台焦点，为避免误打到别的窗口，本次不发任何按键")
        root.destroy()
        return 0

    cfg = Config({
        "source": {"script": script, "segment_mode": "all", "indent_mode": "keep",
                   "resume_last_position": False},
        "timing": {"start_delay_ms": 0, "char_delay_ms": 12, "char_delay_jitter_pct": 30,
                   "thinking_pause_chance": 0.0, "burst_mode": False, "newline_pause_ms": 10,
                   "punctuation_pause_ms": 10, "space_pause_ms": 5},
        "human": {"typo_rate": 0.15, "transpose_rate": 0.03, "double_char_rate": 0.03,
                  "late_notice_rate": 0.3, "fix_delay_min_ms": 30, "fix_delay_max_ms": 80},
        "safety": {"pause_on_focus_change": True, "max_chars_per_trigger": 0},
        "behavior": {"newline_mode": "enter"},
    }, os.path.join(TMP, "e2e-config.json"))

    engine = TypingEngine(cfg, sink=SendInputSink())
    engine.trigger()
    deadline = time.time() + 30
    while engine.status()["typing"] and time.time() < deadline:
        root.update()
        time.sleep(0.02)
    root.update()
    typed = box.get("1.0", "end-1c")
    root.destroy()

    if typed == TEXT:
        print("[PASS] 真实 SendInput 注入：目标窗口收到的文本与脚本完全一致")
        print("       收到：%r" % typed)
        return 0
    print("[FAIL] 注入结果不一致")
    print("       期望：%r" % TEXT)
    print("       收到：%r" % typed)
    return 1


if __name__ == "__main__":
    sys.exit(main())
