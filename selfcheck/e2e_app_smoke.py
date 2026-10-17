# -*- coding: utf-8 -*-
"""整机冒烟测试：真的把程序启动起来，按两种窗口模式各验证一遍。

检查点：
  1. 进程能起来且不报错（用 pythonw，天然没有控制台窗口）；
  2. 托盘窗口永远带 WS_EX_TOOLWINDOW（托盘窗口绝不能进任务栏）；
  3. 设置窗口按配置：
       show_in_taskbar=True  -> 普通窗口（有任务栏按钮、标题栏有最小化/最大化）
       show_in_taskbar=False -> 工具窗口（完全不占任务栏）
  4. 日志里出现“热键已生效”，且没有启动错误；
  5. 通过托盘窗口的消息让程序干净退出。
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROGRAM_DIR = os.path.dirname(HERE)
if PROGRAM_DIR not in sys.path:
    sys.path.insert(0, PROGRAM_DIR)

from autotyper import paths  # noqa: E402
from autotyper.tray import TrayIcon, WM_QUIT_APP  # noqa: E402

# 自测专用：独立的单实例名与托盘窗口类名，避免和用户正在运行的实例互相干扰
SMOKE_MUTEX = "Local\\AutoTyperSmokeTest_9f3c"
SMOKE_TRAY_CLASS = "AutoTyperSmokeTrayWnd"
from autotyper.win32_input import get_ex_style, user32  # noqa: E402

WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
SETTINGS_TITLE = "打字助手 · 设置"


def pythonw() -> str:
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        candidate = exe[:-len("python.exe")] + "pythonw.exe"
        if os.path.exists(candidate):
            return candidate
    return exe


def wait_gone(handle_getter, seconds: float = 10.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if not handle_getter():
            return True
        time.sleep(0.3)
    return False


def run_one(label: str, show_in_taskbar: bool) -> list:
    failures = []
    tmp = os.path.join(PROGRAM_DIR, ".selftest-tmp")
    os.makedirs(tmp, exist_ok=True)
    config_path = os.path.join(tmp, "smoke-%s.json" % ("taskbar" if show_in_taskbar else "stealth"))
    with open(config_path, "w", encoding="utf-8") as handle:
        json.dump({
            "source": {"mode": "steps"},
            "hotkeys": {"enabled": True, "start_key": "down", "stop_key": "up", "panic_key": "f12"},
            "behavior": {"start_hidden": True, "show_tray_icon": True, "autostart": False,
                         "show_in_taskbar": show_in_taskbar},
            "safety": {"log_to_file": True},
        }, handle, ensure_ascii=False, indent=2)

    log_path = paths.LOG_PATH
    before = os.path.getsize(log_path) if os.path.exists(log_path) else 0

    startupinfo = subprocess.STARTUPINFO()
    child_env = dict(os.environ)
    child_env["AUTOTYPER_MUTEX"] = SMOKE_MUTEX
    child_env["AUTOTYPER_TRAY_CLASS"] = SMOKE_TRAY_CLASS
    process = subprocess.Popen(
        [pythonw(), os.path.join(PROGRAM_DIR, "AutoTyper.pyw"), "--hidden", "--config", config_path],
        cwd=PROGRAM_DIR, startupinfo=startupinfo, env=child_env,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))

    tray_hwnd = 0
    settings_hwnd = 0
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            time.sleep(0.4)
            tray_hwnd = int(user32.FindWindowW(SMOKE_TRAY_CLASS, None) or 0)
            settings_hwnd = int(user32.FindWindowW(None, SETTINGS_TITLE) or 0)
            if tray_hwnd and settings_hwnd:
                break
        if not tray_hwnd:
            failures.append("[%s] 没有找到托盘窗口（程序可能没起来）" % label)
        else:
            style = get_ex_style(tray_hwnd)
            if not style & WS_EX_TOOLWINDOW:
                failures.append("[%s] 托盘窗口缺少 WS_EX_TOOLWINDOW（会出现在任务栏）" % label)
            if style & WS_EX_APPWINDOW:
                failures.append("[%s] 托盘窗口带 WS_EX_APPWINDOW（会出现在任务栏）" % label)

        if not settings_hwnd:
            failures.append("[%s] 没有找到设置窗口" % label)
        else:
            style = get_ex_style(settings_hwnd)
            has_tool = bool(style & WS_EX_TOOLWINDOW)
            has_app = bool(style & WS_EX_APPWINDOW)
            if show_in_taskbar:
                if has_tool or not has_app:
                    failures.append("[%s] 设置窗口应当是普通窗口（进任务栏、有最小化/最大化），实际 exstyle=0x%X"
                                    % (label, style))
                else:
                    print("  [%s] 设置窗口 exstyle=0x%X：普通窗口，任务栏有按钮、标题栏有最小化/最大化" % (label, style))
            else:
                if not has_tool or has_app:
                    failures.append("[%s] 设置窗口应当是工具窗口（不占任务栏），实际 exstyle=0x%X"
                                    % (label, style))
                else:
                    print("  [%s] 设置窗口 exstyle=0x%X：工具窗口，完全不占任务栏" % (label, style))

        time.sleep(1.0)
        tail = ""
        if os.path.exists(log_path):
            with open(log_path, "rb") as handle:
                tail = handle.read()[before:].decode("utf-8", "replace")
        if "热键已生效" not in tail:
            failures.append("[%s] 日志里没有“热键已生效”，全局热键可能没装上" % label)
        if "Traceback (most recent call last)" in tail or " 启动失败：" in tail:
            failures.append("[%s] 日志里出现启动错误" % label)
            print(tail[-600:])
    finally:
        if tray_hwnd:
            user32.PostMessageW(tray_hwnd, WM_QUIT_APP, 0, 0)
        try:
            process.wait(timeout=12)
        except Exception:
            process.kill()
            failures.append("[%s] 程序没有响应退出指令，被强制结束" % label)
        if not wait_gone(lambda: int(user32.FindWindowW(SMOKE_TRAY_CLASS, None) or 0)):
            failures.append("[%s] 退出后托盘窗口仍然存在" % label)
    return failures


def main() -> int:
    print("模式 A：在任务栏显示设置窗口（默认，方便随时点回来）")
    failures = run_one("任务栏模式", True)
    print("模式 B：完全不占任务栏（工具窗口 + 托盘）")
    failures += run_one("隐形模式", False)

    if failures:
        print("[FAIL] 整机冒烟测试")
        for item in failures:
            print("       - " + item)
        return 1
    print("[PASS] 整机冒烟测试：两种窗口模式都与设置一致，热键生效，退出干净")
    return 0


if __name__ == "__main__":
    sys.exit(main())
