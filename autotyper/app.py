# -*- coding: utf-8 -*-
"""主程序：托盘、全局热键、打字引擎、设置界面的接线。"""

from __future__ import annotations

import ctypes
import os
import queue
import sys
import tempfile
import threading
import time
import tkinter as tk
from ctypes import wintypes
from tkinter import messagebox

from . import paths
from .config import Config, load as load_config, save_atomic
from .engine import TypingEngine
from .gui import SettingsWindow, setup_look
from .hotkeys import GlobalHotkeys
from .tray import TrayIcon, MenuItem, WM_SHOW_SETTINGS, WM_QUIT_APP
from .version import APP_NAME, __version__
from .win32_input import (SendInputSink, enable_dpi_awareness, foreground_info, get_ex_style,
                          hide_from_taskbar, integrity_level, show_in_taskbar,
                          toplevel_hwnd, try_take_foreground, user32)

MUTEX_NAME = os.environ.get("AUTOTYPER_MUTEX") or r"Local\AutoTyperStandalone_v1_1_0"
ERROR_ALREADY_EXISTS = 183
WM_CLOSE = 0x0010

STATE_LABELS = {"idle": "待命", "typing": "正在打字", "done": "已打完", "stopped": "已停止"}


def _devnull_guard() -> None:
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def setup_logging(config: Config) -> None:
    if not bool(config.get("safety.log_to_file", True)):
        return
    try:
        import logging
        from logging.handlers import RotatingFileHandler
        os.makedirs(paths.LOG_DIR, exist_ok=True)
        root = logging.getLogger()
        if root.handlers:
            return
        handler = RotatingFileHandler(paths.LOG_PATH, maxBytes=512 * 1024,
                                      backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    except Exception:
        pass


def _autostart_target() -> str:
    if getattr(sys, "frozen", False):
        return '"%s"' % os.path.abspath(sys.executable)
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    launcher = pythonw if os.path.exists(pythonw) else sys.executable
    return '"%s" "%s"' % (launcher, os.path.join(paths.PROGRAM_DIR, "AutoTyper.pyw"))


def autostart_present() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            value, _ = winreg.QueryValueEx(key, "AutoTyperStandalone")
            return bool(value)
    except Exception:
        return False


def set_autostart(enabled: bool) -> bool:
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                              r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            if enabled:
                winreg.SetValueEx(key, "AutoTyperStandalone", 0, winreg.REG_SZ, _autostart_target())
            else:
                try:
                    winreg.DeleteValue(key, "AutoTyperStandalone")
                except FileNotFoundError:
                    pass
        return True
    except Exception:
        return False


class AutoTyperApp:
    def __init__(self, config_path: str = "", start_hidden: bool = True,
                 show_settings: bool = False):
        self.log_lines = []
        self.ui_queue: "queue.Queue[tuple]" = queue.Queue()
        self.cfg = load_config(config_path or paths.CONFIG_PATH)
        setup_logging(self.cfg)
        enable_dpi_awareness()
        self._single = self._acquire_single_instance()
        if not self._single[0]:
            self._signal_existing()
            raise SystemExit(0)

        self.log("打字助手 v%s 已启动（配置文件：%s，完整性级别：%s，托盘：%s）"
                 % (__version__, paths.display_path(self.cfg.path), integrity_level(),
                    "正常" if integrity_level() != "Low" else "受限"))
        self._hide_hint_shown = False
        self.input_mode = str(self.cfg.get("behavior.input_mode", "auto") or "auto")
        self.sink = SendInputSink(input_mode=self.input_mode)
        self.engine = TypingEngine(self.cfg, sink=self.sink, on_event=self._on_engine_event,
                                   logger=self.log)
        self.hotkeys = GlobalHotkeys(
            self._binding_map(),
            require_modifier=str(self.cfg.get("hotkeys.require_modifier", "none")),
            swallow=bool(self.cfg.get("hotkeys.swallow_keys", True)),
            gate=self._hotkey_gate, swallow_gate=self._swallow_gate,
            on_action=self._on_hotkey, on_unlock=self._on_key_unlock,
            lock_hold_key=str(self.cfg.get("lock_keys.hold_unlock_key", "esc")),
            lock_hold_seconds=float(self.cfg.get("lock_keys.hold_unlock_seconds", 2.0)),
            logger=self.log)
        self.settings: SettingsWindow | None = None
        self.tray = TrayIcon(tooltip=APP_NAME, menu_provider=self._tray_menu,
                             on_command=self._on_tray_command, on_show=self.show_window,
                             on_quit=self.quit_app)

        self.root = tk.Tk()
        self.root.withdraw()
        setup_look(self.root)
        self.settings = SettingsWindow(self, self.root)
        self._apply_window_style()
        self._start_hotkeys()
        self.apply_autostart()
        self.root.after(200, self._drain_queue)
        self.root.after(300, self._tick)
        started = self.tray.start(timeout=5.0)
        if not started:
            self.log("托盘图标创建失败：%s（不影响打字，但请用窗口按钮退出）" % self.tray.error)
        if not start_hidden or show_settings:
            self.show_window()
        # 安全起见：每次启动都从“未锁定”开始，绝不自动锁键盘
        if bool(self.cfg.get("lock_keys.enabled", False)):
            self.cfg.set("lock_keys.enabled", False)
            try:
                self.cfg.save()
            except Exception:
                pass

    # ------------------------------------------------------------ 单实例
    def _acquire_single_instance(self):
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.restype = wintypes.HANDLE
            kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
            handle = kernel32.CreateMutexW(None, True, MUTEX_NAME)
            self._mutex = handle
            if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
                return False, "已经有一个实例在运行"
            return True, ""
        except Exception as exc:
            return True, "互斥体创建失败（忽略）：%s" % exc

    def _signal_existing(self) -> None:
        try:
            hwnd = int(user32.FindWindowW(TrayIcon.CLASS_NAME, None) or 0)
            if hwnd:
                user32.PostMessageW(hwnd, WM_SHOW_SETTINGS, 0, 0)
        except Exception:
            pass

    # ------------------------------------------------------------ 日志
    def log(self, message: str, level: str = "info") -> None:
        self.log_lines.append("%s %s" % (time.strftime("%H:%M:%S"), message))
        self.log_lines = self.log_lines[-200:]
        try:
            import logging
            getattr(logging.getLogger(), level if level in ("info", "warning", "error") else "info")(message)
        except Exception:
            pass

    # ------------------------------------------------------------ 热键
    def _binding_map(self) -> dict:
        return {"start": str(self.cfg.get("hotkeys.start_key", "down") or ""),
                "stop": str(self.cfg.get("hotkeys.stop_key", "up") or ""),
                "panic": str(self.cfg.get("hotkeys.panic_key", "f12") or "")}

    def _hotkey_gate(self) -> bool:
        if not bool(self.cfg.get("hotkeys.enabled", True)):
            return False
        try:
            if int(foreground_info().get("pid") or 0) == os.getpid():
                return False
        except Exception:
            pass
        if not bool(self.cfg.get("hotkeys.only_when_target", False)):
            return True
        target = str(self.cfg.get("hotkeys.target_process", "") or "").strip().lower()
        title = str(self.cfg.get("hotkeys.target_title_contains", "") or "").strip().lower()
        try:
            info = foreground_info()
        except Exception:
            return False
        process = str(info.get("process") or "").lower()
        window_title = str(info.get("title") or "").lower()
        return bool(target and target in process) or bool(title and title in window_title)

    def _swallow_gate(self) -> bool:
        if not bool(self.cfg.get("hotkeys.swallow_keys", True)):
            return False
        mode = str(self.cfg.get("hotkeys.swallow_mode", "target") or "target").lower()
        if mode == "off":
            return False
        if mode == "always":
            return True
        target = str(self.cfg.get("hotkeys.target_process", "") or "").strip().lower()
        title = str(self.cfg.get("hotkeys.target_title_contains", "") or "").strip().lower()
        if not target and not title:
            return False
        try:
            info = foreground_info()
        except Exception:
            return False
        process = str(info.get("process") or "").lower()
        window_title = str(info.get("title") or "").lower()
        return bool(target and target in process) or bool(title and title in window_title)

    def _start_hotkeys(self) -> None:
        self.hotkeys.set_bindings(self._binding_map())
        self.hotkeys.set_lock_hold(str(self.cfg.get("lock_keys.hold_unlock_key", "esc")),
                                   float(self.cfg.get("lock_keys.hold_unlock_seconds", 2.0)))
        ok, message = self.hotkeys.start()
        self.log(message, "info" if ok else "warning")
        try:
            self.settings.refresh_hotkey_live()
        except Exception:
            pass

    def _restart_hotkeys(self) -> None:
        try:
            self.hotkeys.stop()
        except Exception:
            pass
        self._start_hotkeys()

    def apply_hotkeys_live(self) -> None:
        self.cfg.save()
        self._restart_hotkeys()
        try:
            self.settings.refresh_hotkey_live()
        except Exception:
            pass

    def toggle_hotkeys(self) -> None:
        enabled = not bool(self.cfg.get("hotkeys.enabled", True))
        self.cfg.set("hotkeys.enabled", enabled)
        self.cfg.save()
        if enabled:
            self._restart_hotkeys()
        else:
            self.hotkeys.stop()
        self.log("全局热键已%s" % ("开启" if enabled else "暂停"))

    def _on_hotkey(self, action: str) -> None:
        self.ui_queue.put(("hotkey", action))

    def _on_key_unlock(self, reason: str) -> None:
        self.ui_queue.put(("key_unlock", reason))

    # ------------------------------------------------------------ 演示模式
    def toggle_key_lock(self, enabled=None) -> None:
        target = (not self.hotkeys.key_locked) if enabled is None else bool(enabled)
        self._apply_key_lock(target)

    def _apply_key_lock(self, enabled: bool, reason: str = "") -> None:
        enabled = bool(enabled)
        try:
            self.hotkeys.set_key_lock(enabled)
        except Exception:
            pass
        self.cfg.set("lock_keys.enabled", enabled)
        try:
            self.cfg.save()
        except Exception:
            pass
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONHAND if enabled else winsound.MB_ICONASTERISK)
        except Exception:
            pass
        if enabled:
            self.log("演示模式已开启：字母键已锁住（按住 Esc 2 秒 / 托盘菜单 / 退出程序 都能解锁）",
                     "warning")
            try:
                self.tray.notify(APP_NAME, "字母键已锁住：按住 Esc 2 秒即可解锁")
            except Exception:
                pass
        else:
            message = "演示模式已关闭：字母键恢复正常" + (("（%s）" % reason) if reason else "")
            self.log(message)
            try:
                self.tray.notify(APP_NAME, "字母键已恢复")
            except Exception:
                pass
        try:
            self.settings.refresh_lock_state()
        except Exception:
            pass

    # ------------------------------------------------------------ 窗口
    def _apply_window_style(self) -> None:
        try:
            self.root.update_idletasks()
            hwnd = toplevel_hwnd(self.root.winfo_id())
            self._hwnd = hwnd
            if bool(self.cfg.get("behavior.show_in_taskbar", True)):
                show_in_taskbar(hwnd)
            else:
                hide_from_taskbar(hwnd)
        except Exception:
            pass

    def show_window(self) -> None:
        try:
            self._apply_window_style()
            self.root.deiconify()
            self.root.lift()
            try:
                self.root.attributes("-topmost", True)
                self.root.after(300, lambda: self.root.attributes("-topmost", False))
            except Exception:
                pass
            hwnd = getattr(self, "_hwnd", 0) or toplevel_hwnd(self.root.winfo_id())
            try_take_foreground(hwnd)
        except Exception:
            pass

    def hide_window(self) -> None:
        try:
            self.root.withdraw()
        except Exception:
            pass
        if not self._hide_hint_shown:
            self._hide_hint_shown = True
            self.log("窗口已收进托盘，程序仍在后台待命（按 ↓ 可打字；要退出请右键托盘→退出程序）")
            try:
                self.tray.notify(APP_NAME,
                                 "已收进托盘，程序仍在后台待命：按 ↓ 就能打字；"
                                 "要退出请右键托盘图标 → 退出程序。")
            except Exception:
                pass

    def _on_close_button(self) -> None:
        if bool(self.cfg.get("behavior.close_to_tray", True)):
            self.hide_window()
            return
        self.quit_app()

    # ------------------------------------------------------------ 消息循环
    def _on_tray_command(self, ident: str) -> None:
        self.ui_queue.put(("command", ident))

    def _on_engine_event(self, action: str, payload=None) -> None:
        self.ui_queue.put(("engine_event", (action, payload)))

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                try:
                    if kind == "hotkey":
                        self._handle_action(str(payload))
                    elif kind == "command":
                        self._handle_action(str(payload))
                    elif kind == "key_unlock":
                        self._apply_key_lock(False, reason=str(payload or ""))
                    elif kind == "engine_event":
                        action, data = payload
                        if action == "segment_done":
                            self._notify_segment_done(data or {})
                except Exception as exc:
                    self.log("处理消息出错（%s/%s）：%s" % (kind, payload, exc))
        except queue.Empty:
            pass
        except Exception:
            pass
        try:
            self.root.after(150, self._drain_queue)
        except Exception:
            pass

    def _handle_action(self, ident: str) -> None:
        if ident == "start":
            self._start_typing()
        elif ident == "stop":
            self.engine.stop()
            self.log("已停止")
        elif ident == "panic":
            self.engine.stop()
            if self.hotkeys.key_locked:
                self._apply_key_lock(False, reason="紧急停止")
            self.log("紧急停止（F12 或设置的紧急键）")
        elif ident == "show":
            self.show_window()
        elif ident == "hide":
            self.hide_window()
        elif ident == "reset":
            self.reset_progress()
        elif ident == "reload":
            self.reload_script()
        elif ident == "toggle_hotkeys":
            self.toggle_hotkeys()
        elif ident == "toggle_lock":
            self.toggle_key_lock()
        elif ident == "quit":
            self.quit_app()

    def _start_typing(self) -> None:
        ok, message = self.engine.trigger()
        if ok:
            self.log(message)
        else:
            self.log(message, "warning")
            try:
                self.settings.hint_var.set(message)
            except Exception:
                pass

    # ------------------------------------------------------------ 状态刷新
    def _status_payload(self) -> dict:
        try:
            status = self.engine.status()
        except Exception:
            return {"state_label": "待命"}
        return {"state_label": STATE_LABELS.get(status.get("state"), "待命"),
                "segment": status.get("segment"),
                "segments": status.get("total_segments"),
                "step_name": status.get("step_name"),
                "percent": status.get("percent"),
                "position": status.get("position"),
                "total": status.get("total"),
                "steps_mode": status.get("steps_mode"),
                "message": ""}

    def _tick(self) -> None:
        try:
            payload = self._status_payload()
            if self.settings is not None:
                self.settings.on_status(payload)
            if self.tray.running:
                tooltip = "%s · %s" % (APP_NAME, payload.get("state_label") or "待命")
                self._last_tooltip = getattr(self, "_last_tooltip", "")
                if tooltip != self._last_tooltip:
                    self._last_tooltip = tooltip
                    self.tray.update_tooltip(tooltip)
        except Exception:
            pass
        try:
            self.root.after(300, self._tick)
        except Exception:
            pass

    # ------------------------------------------------------------ 完成提示
    def _notify_segment_done(self, payload: dict) -> None:
        name = payload.get("name") or ""
        if payload.get("finished"):
            self.log("全部打完了（共 %d 层）" % (payload.get("total") or 0))
            message = "全部打完了"
        else:
            self.log("第 %d/%d 层打完了：%s" % (payload.get("index") or 0, payload.get("total") or 0, name))
            message = "第 %d/%d 层打完了%s" % (payload.get("index") or 0, payload.get("total") or 0,
                                              ("：" + name) if name else "")
        if bool(self.cfg.get("behavior.sound_on_done", True)):
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except Exception:
                pass
        if bool(self.cfg.get("behavior.balloon_on_done", False)):
            try:
                self.tray.notify(APP_NAME, message)
            except Exception:
                pass

    # ------------------------------------------------------------ 设置
    def apply_from_ui(self, reload_ui: bool = False) -> None:
        try:
            if self.settings is not None:
                self.settings.collect()
        except Exception as exc:
            self.log("收集界面设置出错：%s" % exc)
        self.cfg.save()
        mode = str(self.cfg.get("behavior.input_mode", "auto") or "auto")
        self.input_mode = mode
        try:
            self.sink.input_mode = mode
        except Exception:
            pass
        self.engine.reload_text(keep_position=True)
        self._restart_hotkeys()
        self.apply_autostart()
        self._apply_window_style()
        want_lock = bool(self.cfg.get("lock_keys.enabled", False))
        if want_lock != self.hotkeys.key_locked:
            self._apply_key_lock(want_lock)
        self.log("设置已保存并应用")

    def reset_progress(self) -> None:
        self.engine.stop()
        self.engine.reset()
        self.log("进度已重置，从第一段重新开始")
        try:
            self.settings.hint_var.set("进度已重置")
        except Exception:
            pass

    def reload_script(self) -> None:
        self.engine.reload_text(keep_position=False)
        self.log("已重新载入内容")

    def test_type(self) -> None:
        self.hide_window()
        self.log("开始试打几个字（请先点到要打字的程序）")
        try:
            self.tray.notify(APP_NAME, "请点一下要打字的程序，2 秒后开始试打几个字")
        except Exception:
            pass

        def later():
            time.sleep(2.2)
            ok, message = self.engine.trigger()
            self.ui_queue.put(("hotkey", "noop"))
            self.log(message, "info" if ok else "warning")

        threading.Thread(target=later, daemon=True).start()

    def apply_autostart(self) -> None:
        try:
            wanted = bool(self.cfg.get("behavior.autostart", False))
            if wanted and not autostart_present():
                set_autostart(True)
            elif not wanted and autostart_present():
                set_autostart(False)
        except Exception:
            pass

    # ------------------------------------------------------------ 托盘菜单
    def _tray_menu(self):
        items = [MenuItem("show", "打开设置界面" + ("（F9）" if False else ""))]
        items.append(MenuItem("-", "-", separator=True))
        items.append(MenuItem("start", "开始 / 继续（按一下）"))
        items.append(MenuItem("stop", "停止"))
        items.append(MenuItem("panic", "紧急停止"))
        items.append(MenuItem("-", "-", separator=True))
        items.append(MenuItem("reset", "重置进度（从头开始）"))
        items.append(MenuItem("reload", "重新载入内容"))
        items.append(MenuItem("toggle_hotkeys", "暂停全局热键",
                              checked=not bool(self.cfg.get("hotkeys.enabled", True))))
        items.append(MenuItem("toggle_lock", "锁定字母键（演示模式）",
                              checked=self.hotkeys.key_locked))
        items.append(MenuItem("-", "-", separator=True))
        items.append(MenuItem("quit", "退出程序"))
        return items

    # ------------------------------------------------------------ 退出
    def quit_app(self) -> None:
        self.log("已退出")
        try:
            self.engine.stop()
        except Exception:
            pass
        try:
            self.hotkeys.set_key_lock(False)
            self.hotkeys.stop()
        except Exception:
            pass
        try:
            self.cfg.save()
        except Exception:
            pass
        try:
            self.tray.stop()
        except Exception:
            pass
        try:
            self.root.quit()
            self.root.destroy()
        except Exception:
            pass


def run_selftest(with_windows: bool) -> int:
    import importlib.util
    # 自测时不要在程序目录里生成 .pyc（里面会记录本机路径），
    # 否则一次自测就会把本机用户名/目录写进程序目录。
    sys.dont_write_bytecode = True
    target = os.path.join(paths.PROGRAM_DIR, "selfcheck", "offline.py")
    if not os.path.exists(target):
        print("找不到自测脚本：%s" % paths.display_path(target))
        return 2
    spec = importlib.util.spec_from_file_location("autotyper_selftest", target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return int(module.run_all(with_windows=with_windows))


def main(argv=None) -> int:
    _devnull_guard()
    argv = list(sys.argv[1:] if argv is None else argv)
    config_path = ""
    for index, item in enumerate(argv):
        if item == "--config" and index + 1 < len(argv):
            config_path = argv[index + 1]
    if "--version" in argv or "-v" in argv:
        print("%s %s" % (APP_NAME, __version__))
        return 0
    if "--selftest" in argv:
        paths.seed_bundled_files()
        return run_selftest("--with-windows" in argv)
    if "--dry-run" in argv:
        cfg = load_config(config_path or paths.CONFIG_PATH)
        engine = TypingEngine(cfg, sink=SendInputSink())
        for line in engine.preview(1):
            print(line)
        return 0
    paths.seed_bundled_files()
    try:
        # 切到临时目录：输入法/某些软件会往“当前目录”写调试文件，别让它污染程序目录
        os.chdir(tempfile.gettempdir())
    except Exception:
        pass
    try:
        app = AutoTyperApp(config_path=config_path,
                           start_hidden=("--settings" not in argv),
                           show_settings=("--settings" in argv))
    except SystemExit:
        return 0
    except Exception as exc:
        import traceback
        traceback.print_exc()
        try:
            messagebox.showerror("打字助手启动失败", str(exc))
        except Exception:
            pass
        return 1
    try:
        app.root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            app.quit_app()
        except Exception:
            pass
    return 0
