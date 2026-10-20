# -*- coding: utf-8 -*-
"""设置界面逻辑测试：楼层编辑、百分比换算、游戏式改键（含旧键位解绑）。

界面不参与打字，但它是用户配置一切的唯一入口，所以单独验证：
  * 楼层的新增/删除/移动/内容保存，最终能不能正确写进配置；
  * 百分比字段（错字率 2.5% ↔ 0.025）来回转换是否正确；
  * 改键之后旧键位是不是真的没了（这是用户明确提出的要求）；
  * ③ 页的“用户打字习惯校准”：采集/分析/套用能不能真的改对参数、刷新状态。
"""

from __future__ import annotations

import json as _json
import os
import sys
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

HERE = os.path.dirname(os.path.abspath(__file__))
PROGRAM_DIR = os.path.dirname(HERE)
if PROGRAM_DIR not in sys.path:
    sys.path.insert(0, PROGRAM_DIR)

from autotyper import habits  # noqa: E402
from autotyper import paths  # noqa: E402
from autotyper.config import Config  # noqa: E402
from autotyper.engine import TypingEngine  # noqa: E402
from autotyper.gui import SettingsWindow, pretty_key  # noqa: E402

TMP = os.path.join(PROGRAM_DIR, ".selftest-tmp")
paths.STATE_PATH = os.path.join(TMP, "gui-state.json")

RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition)))
    print("[%s] %s%s" % ("PASS" if condition else "FAIL", name, (" — " + detail) if detail else ""))


class FakeHotkeys:
    """模拟热键模块：label_for() 返回“当前真正生效”的按键。"""

    def __init__(self):
        self.running = True
        self.table = {"start": "down", "stop": "up", "panic": "f12", "reset": ""}

    def label_for(self, action):
        return self.table.get(action, "")


class FakeApp:
    def __init__(self, cfg):
        self.cfg = cfg
        self.engine = TypingEngine(cfg)
        self.hotkeys = FakeHotkeys()
        self.saved = 0
        self.live_calls = 0

    def apply_from_ui(self, reload_ui=False):
        self.saved += 1

    def apply_hotkeys_live(self):
        """忠实模拟真实实现：收集界面上的按键 -> 重建“生效表”。"""
        self.live_calls += 1
        if getattr(self, "settings", None) is not None:
            self.settings.collect_hotkeys()
        self.hotkeys.table = {
            "start": str(self.cfg.get("hotkeys.start_key") or ""),
            "stop": str(self.cfg.get("hotkeys.stop_key") or ""),
            "panic": str(self.cfg.get("hotkeys.panic_key") or ""),
            "reset": str(self.cfg.get("hotkeys.reset_key") or ""),
        }
        if getattr(self, "settings", None) is not None:
            self.settings.refresh_hotkey_live()

    def hide_window(self):
        pass

    def quit_app(self):
        pass

    def reset_progress(self):
        pass

    def reload_script(self):
        pass

    def test_type(self):
        pass


class FakeAppNoCollect(FakeApp):
    """模拟真实 AutoTyperApp：apply_hotkeys_live 不会再替界面 collect（历史 bug 场景）。"""

    def apply_hotkeys_live(self):
        self.live_calls += 1
        self.hotkeys.table = {
            "start": str(self.cfg.get("hotkeys.start_key") or ""),
            "stop": str(self.cfg.get("hotkeys.stop_key") or ""),
            "panic": str(self.cfg.get("hotkeys.panic_key") or ""),
            "reset": str(self.cfg.get("hotkeys.reset_key") or ""),
        }
        if getattr(self, "settings", None) is not None:
            self.settings.refresh_hotkey_live()


def main() -> int:
    # 输出被管道/重定向接管时，Windows 上的 Python 默认按 GBK 编码，
    # 打印 ⏎ 这类符号会 UnicodeEncodeError；改成 UTF-8 输出（控制台直跑不受影响）。
    try:
        if not sys.stdout.isatty():
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    os.makedirs(TMP, exist_ok=True)
    cfg = Config({}, os.path.join(TMP, "gui-config.json"))
    app = FakeApp(cfg)
    root = tk.Tk()
    root.withdraw()
    window = SettingsWindow(app, root)
    app.settings = window
    app.hotkeys.table = {"start": str(cfg.get("hotkeys.start_key")),
                         "stop": str(cfg.get("hotkeys.stop_key")),
                         "panic": str(cfg.get("hotkeys.panic_key")),
                         "reset": str(cfg.get("hotkeys.reset_key"))}

    # ---- 显示是否正常（曾被裁字）----
    style = ttk.Style()
    row_height = int(float(style.lookup("Treeview", "rowheight") or 0))
    linespace = int(tkfont.nametofont("TkDefaultFont").metrics("linespace"))
    check("显示：列表行高足够，文字不会被上下裁掉",
          row_height >= linespace + 4, "行高=%d 字体行高=%d" % (row_height, linespace))
    check("显示：界面字体已经统一设置",
          str(tkfont.nametofont("TkDefaultFont").cget("family")) != "", 
          str(tkfont.nametofont("TkDefaultFont").cget("family")))
    check("显示：列表默认能显示多层（不用滚动就能看到）",
          int(window.step_tree.cget("height")) >= 5, str(window.step_tree.cget("height")))

    # ---- 底部按钮必须真的可见（曾经被窗口高度挤到屏幕外）----
    window.root.deiconify()
    window.root.update()
    quit_btn = getattr(window, "quit_button", None)
    mapped = bool(quit_btn) and quit_btn.winfo_ismapped() and quit_btn.winfo_height() > 10
    check("界面上“退出程序”按钮可见", mapped,
          "mapped=%s h=%s" % (mapped, quit_btn.winfo_height() if quit_btn else "?"))

    from autotyper.config import DEFAULTS
    check("默认设置：点 × 直接退出程序（关窗口=关程序）",
          DEFAULTS["behavior"]["close_to_tray"] is False,
          str(DEFAULTS["behavior"]["close_to_tray"]))
    check("界面上有“隐藏到托盘”按钮（想后台待命时用）",
          bool(getattr(window, "hide_button", None))
          and window.hide_button.winfo_ismapped(), "mapped")
    check("窗口有最小尺寸，拖不窄（不会挤断内容）",
          window.root.minsize() == (880, 620), str(window.root.minsize()))
    window.root.withdraw()

    # ---- 楼层框能否拖动放大 ----
    paned = getattr(window, "steps_paned", None)
    check("楼层框可以拖动放大（上下可调分隔）", paned is not None and paned.winfo_height() > 80,
          "paned=%s" % (paned is not None and paned.winfo_height()))
    if paned is not None:
        try:
            total = max(120, int(paned.winfo_height()))
            target = int(total * 0.65)
            paned.sashpos(0, target)
            paned.update_idletasks()
            got = int(paned.sashpos(0))
            check("拖动后楼层框真的变大（分隔条位置生效）", abs(got - target) <= 6, "期望 %d 实际 %d" % (target, got))
            window._save_paned()
            try:
                ratio = float(window.cfg.get("ui.paned_ratio"))
            except (TypeError, ValueError):
                ratio = -1
            check("楼层框大小会记住（写进配置）", 0.05 < ratio < 0.95, str(ratio))
        except Exception as exc:
            check("拖动后楼层框真的变大（分隔条位置生效）", False, str(exc))

    # ---- 楼层 ----
    check("楼层：默认就有 3 层", len(window.steps) == 3, str(len(window.steps)))
    check("楼层：列表里显示 3 行", len(window.step_tree.get_children()) == 3)
    window.step_text.delete("1.0", "end")
    window.step_text.insert("1.0", "print('改过了')")
    window.step_name_var.set("新名字")
    window.save_current_step()
    check("楼层：编辑后写回当前层",
          window.steps[0]["text"] == "print('改过了')" and window.steps[0]["name"] == "新名字",
          window.steps[0]["name"])
    window.add_step()
    check("楼层：新增一层", len(window.steps) == 4 and len(window.step_tree.get_children()) == 4)
    window.step_text.insert("1.0", "print(4)")
    window.save_current_step()
    window.move_step(-1)
    check("楼层：上移生效", window.steps[2]["text"] == "print(4)", window.steps[2]["text"])
    window.delete_step()
    check("楼层：删除一层", len(window.steps) == 3, str(len(window.steps)))
    check("楼层：编辑后立刻写进配置（不用再找按钮）",
          len(cfg.steps()) == 3 and cfg.steps()[0]["text"] == "print('改过了')", str(cfg.steps()[0]))
    rows = app.engine.step_summaries()
    check("楼层：引擎立刻用上新内容（层数与内容都对）",
          len(rows) == 3 and rows[0]["preview"].startswith("print("), str(rows[:1]))
    window.collect()
    check("楼层：保存后配置一致", len(cfg.steps()) == 3, str(len(cfg.steps())))

    # ---- 百分比 ----
    typo_var = window._vars[("human.typo_rate", "float")]
    check("界面：错字率按百分比显示", abs(float(typo_var.get()) - 2.5) < 0.001, typo_var.get())
    typo_var.set("5")
    window._vars[("timing.char_delay_ms", "int")].set(30)
    window.collect()
    check("界面：改成 5% 后配置同步", abs(float(cfg.get("human.typo_rate")) - 0.05) < 1e-9,
          str(cfg.get("human.typo_rate")))
    check("界面：改成 30ms 后配置同步", int(cfg.get("timing.char_delay_ms")) == 30,
          str(cfg.get("timing.char_delay_ms")))

    # ---- 改键（重点）----
    check("改键：默认是 ↓ / ↑ / F12 / 重置未设置",
          window._binding_vars["start"].get() == "down" and window._binding_vars["stop"].get() == "up"
          and window._binding_vars["panic"].get() == "f12"
          and window._binding_vars["reset"].get() == "")
    window._set_binding("start", "f8")
    check("改键：开始键变成 F8", window._binding_vars["start"].get() == "f8")
    check("改键：立刻重新注册了热键", app.live_calls == 1, str(app.live_calls))
    check("改键：旧键位 ↓ 已经不在生效表里", app.hotkeys.table["start"] == "f8",
          str(app.hotkeys.table))
    check("改键：生效提示里写的是 F8", "F8" in window.hotkey_live_var.get(), window.hotkey_live_var.get())

    window._set_binding("stop", "f8")   # 与开始键冲突
    check("改键：重复的键会从另一个动作上清掉",
          window._binding_vars["stop"].get() == "f8" and window._binding_vars["start"].get() == "",
          "start=%r stop=%r" % (window._binding_vars["start"].get(), window._binding_vars["stop"].get()))
    check("改键：提示里说明了自动清空", "清掉" in window.hint_var.get(), window.hint_var.get())
    check("改键：生效表里 start 已解绑", app.hotkeys.table["start"] == "" and app.hotkeys.table["stop"] == "f8",
          str(app.hotkeys.table))

    window._set_binding("panic", "")    # 允许留空
    window.reset_keys()
    check("改键：恢复默认按键", window._binding_vars["start"].get() == "down"
          and window._binding_vars["stop"].get() == "up"
          and window._binding_vars["panic"].get() == "f12"
          and window._binding_vars["reset"].get() == "")
    check("改键：恢复默认后立刻生效", app.hotkeys.table["start"] == "down", str(app.hotkeys.table))

    # ---- 重置进度热键 ----
    window._set_binding("reset", "f9")
    check("重置键：能改成 F9", window._binding_vars["reset"].get() == "f9",
          window._binding_vars["reset"].get())
    check("重置键：立刻写回配置", str(cfg.get("hotkeys.reset_key")) == "f9",
          str(cfg.get("hotkeys.reset_key")))
    check("重置键：生效表里出现", app.hotkeys.table.get("reset") == "f9",
          str(app.hotkeys.table))
    window.reset_keys()

    # ---- 改键：真实捕获路径（旧版点“按下新键”后焦点在按钮上，永远收不到按键）----
    # 说明：Tk 在窗口不可见时 event_generate 的键盘事件不会派发，所以这里临时显示窗口，
    # 模拟用户真实操作（真实使用时窗口本来就是可见的）。
    try:
        window.root.deiconify()
        window.root.update()
    except Exception:
        pass
    window._begin_capture("start")
    check("改键：点“按下新键”后进入捕获状态", window._capture_action == "start",
          str(window._capture_action))
    check("改键：捕获期间窗口级监听已挂上", bool(window.root.bind("<KeyPress>")),
          str(bool(window.root.bind("<KeyPress>"))))
    window.root.event_generate("<KeyPress>", keysym="Shift_L")
    window.root.update()
    check("改键：先按到 Shift 不结束捕获", window._capture_action == "start",
          str(window._capture_action))
    window.root.event_generate("<KeyPress>", keysym="F8")
    window.root.update()
    check("改键：焦点不在输入框上也能识别按下的新键",
          window._binding_vars["start"].get() == "f8", window._binding_vars["start"].get())
    check("改键：捕获结束后自动退出捕获状态", window._capture_action is None,
          str(window._capture_action))
    check("改键：捕获结束后窗口级监听已移除", not window.root.bind("<KeyPress>"))
    check("改键：新键立刻写回配置文件", str(cfg.get("hotkeys.start_key")) == "f8",
          str(cfg.get("hotkeys.start_key")))
    check("改键：生效表也换成新键", app.hotkeys.table["start"] == "f8", str(app.hotkeys.table))
    window.root.event_generate("<KeyPress>", keysym="F9")
    window.root.update()
    check("改键：捕获结束后再按键不会误改", window._binding_vars["start"].get() == "f8",
          window._binding_vars["start"].get())

    window._begin_capture("panic")
    window.root.event_generate("<KeyPress>", keysym="comma")
    window.root.update()
    check("改键：逗号这类符号键也能识别", window._binding_vars["panic"].get() == ",",
          window._binding_vars["panic"].get())
    check("改键：符号键显示名正常", pretty_key(",") == ",", pretty_key(","))
    window.reset_keys()

    window._begin_capture("stop")
    window.root.event_generate("<KeyPress>", keysym="Escape")
    window.root.update()
    check("改键：Esc 取消捕获且不改键",
          window._capture_action is None and window._binding_vars["stop"].get() == "up",
          "%s / %s" % (window._capture_action, window._binding_vars["stop"].get()))
    window.reset_keys()

    window._begin_capture("reset")
    window.root.event_generate("<KeyPress>", keysym="F9")
    window.root.update()
    check("重置键：也能用「按下新键」捕获", window._binding_vars["reset"].get() == "f9",
          window._binding_vars["reset"].get())
    check("重置键：捕获后立刻写回配置", str(cfg.get("hotkeys.reset_key")) == "f9",
          str(cfg.get("hotkeys.reset_key")))
    window.reset_keys()
    try:
        window.root.withdraw()
        window.root.update()
    except Exception:
        pass

    # ---- 改键：真实 app 的 apply 不 collect 时也不会“假生效”----
    root2 = tk.Tk()
    root2.withdraw()
    cfg2 = Config({}, os.path.join(TMP, "gui-config-nocollect.json"))
    app2 = FakeAppNoCollect(cfg2)
    window2 = SettingsWindow(app2, root2)
    app2.settings = window2
    window2._set_binding("start", "f8")
    check("改键：真实 app 不替界面 collect 时也真正生效",
          str(cfg2.get("hotkeys.start_key")) == "f8" and app2.hotkeys.table["start"] == "f8",
          "%r / %r" % (cfg2.get("hotkeys.start_key"), app2.hotkeys.table))
    root2.destroy()

    # ---- 保存 / 载入本地演示 ----
    demo_path = os.path.join(TMP, "local-demo.demo.json")
    demo_count = window._write_demo_file(demo_path)
    demo_data = _json.load(open(demo_path, encoding="utf-8"))
    check("保存演示：文件写出成功",
          demo_count == len(window.steps) and os.path.exists(demo_path), str(demo_count))
    check("保存演示：文件头与楼层齐全", demo_data.get("kind") == "autotyper-demo"
          and demo_data.get("app") == "打字助手"
          and isinstance(demo_data.get("steps"), list)
          and len(demo_data["steps"]) == demo_count, str(demo_data.get("kind")))
    check("保存演示：界面按钮存在", bool(getattr(window, "save_demo_button", None))
          and bool(getattr(window, "load_demo_button", None)))
    original_steps = [dict(item) for item in window.steps]
    window._load_demo_steps([{"name": "临时一层", "text": "x = 1"}])
    check("载入演示：临时内容确实被替换", len(window.steps) == 1, str(len(window.steps)))
    loaded_steps = window._read_demo_file(demo_path)
    window._load_demo_steps(loaded_steps)
    check("载入演示：楼层完全还原", window.steps == original_steps,
          str([s.get("name") for s in window.steps]))
    check("载入演示：配置与引擎同步", len(cfg.steps()) == len(original_steps)
          and len(app.engine.step_summaries()) == len(original_steps),
          "%d / %d" % (len(cfg.steps()), len(app.engine.step_summaries())))
    bad_path = os.path.join(TMP, "bad-demo.json")
    with open(bad_path, "w", encoding="utf-8") as handle:
        handle.write('{"foo": 1}')
    try:
        window._read_demo_file(bad_path)
        bad_ok = False
    except ValueError:
        bad_ok = True
    check("载入演示：坏文件给出明确错误", bad_ok)
    plain_path = os.path.join(TMP, "plain-list-demo.json")
    with open(plain_path, "w", encoding="utf-8") as handle:
        handle.write('["print(1)", "print(2)"]')
    check("载入演示：兼容纯字符串列表", len(window._read_demo_file(plain_path)) == 2)

    # ---- 状态显示 ----
    window.on_status({"state_label": "待命", "segment": 2, "segments": 3, "step_name": "第二层",
                      "percent": 33.3, "position": 10, "total": 30, "steps_mode": True, "message": ""})
    check("状态：显示层号与层名", "第 2/3 层" in window.status_var.get() and "第二层" in window.status_var.get(),
          window.status_var.get())
    check("按键显示名漂亮", pretty_key("down") == "↓" and pretty_key("f8") == "F8")

    # ---- 新增选项 ----
    check("新增：每层打完自动回车开关存在", ("source.auto_enter", "bool") in window._vars)
    check("新增：↓/↑ 拦截范围下拉存在", ("hotkeys.swallow_mode", "str") in window._vars)
    check("新增：自动释放修饰键开关存在", ("safety.release_modifiers", "bool") in window._vars)
    check("新增：演示模式开关存在且默认关闭",
          bool(getattr(window, "lock_check", None)) and not bool(window.lock_var.get()))

    # ---- 自动保存（不需要点“保存并应用”）----
    cfg_file = cfg.path
    window._vars[("timing.char_delay_ms", "int")].set(77)
    deadline = time.time() + 6
    while time.time() < deadline:
        window.root.update()
        time.sleep(0.05)
        try:
            data = _json.load(open(cfg_file, encoding="utf-8"))
            if int(data.get("timing", {}).get("char_delay_ms", 0)) == 77:
                break
        except Exception:
            pass
    try:
        data = _json.load(open(cfg_file, encoding="utf-8"))
        saved_value = int(data.get("timing", {}).get("char_delay_ms", 0))
    except Exception:
        saved_value = -1
    check("自动保存：改完设置几秒内自己写进配置（不用点按钮）", saved_value == 77,
          "char_delay_ms=%s" % saved_value)
    check("自动保存：旧配置自动留了 .bak 备份", os.path.exists(cfg_file + ".bak"))
    check("自动保存：界面上显示保存时间", "自动保存" in window.saved_var.get(), window.saved_var.get())

    # ---- 预览 / 演示 ----
    preview = app.engine.preview(5)
    check("界面：干跑预览可用", isinstance(preview, list), "%d 行" % len(preview))

    from selfcheck.offline import DocSink
    from autotyper.humanizer import Humanizer
    import random
    info = app.engine.next_segment_text()
    check("演示：能拿到即将打出的那一层", info is not None and info["total"] == 3, str(info))
    doc = DocSink()
    for action in Humanizer(cfg.section("timing"), cfg.section("human"),
                            rng=random.Random(3)).actions(info["text"], newline_mode="enter"):
        if action.kind == "text":
            doc.type_text(action.text)
        elif action.kind == "key":
            doc.press_key(action.key)
    check("演示：动作流打出的内容与楼层完全一致（含错字修正）",
          doc.text() == info["text"], repr(doc.text()[:40]))
    window._show_demo()
    check("演示：演示窗口能打开", getattr(window, "_demo_window", None) is not None)
    window._close_demo()
    check("演示：关闭后清理干净", getattr(window, "_demo_window", None) is None)

    # ---- ③ 页：用户打字习惯校准（自动适配速度/拟人参数）----
    status_var = getattr(window, "habits_status_var", None)
    check("习惯：③ 页有“我的打字习惯”状态行", status_var is not None)
    initial_state = status_var.get() if status_var is not None else ""
    check("习惯：初始状态显示“还没校准”", "还没校准" in initial_state, initial_state)

    # 造一组“像真人”的按键事件：英文 + 空格/标点/换行 + 两次退格改错
    sample_text = ("The quick brown fox jumps over the lazy dog.\n"
                   "It writes code: def f(x): return x + 1, then # done.\n"
                   'print("hello, world"); # comment')
    events = []
    clock = [1000.0]
    rhythm = [55, 62, 70, 78, 85, 92, 100, 110]
    punctuations = ",.;:!?，。；：！？"

    def record_one(char, keysym, delay):
        clock[0] += float(delay)
        events.append({"t": clock[0], "char": char, "keysym": keysym})

    for index, char in enumerate(sample_text):
        if char == "\n":
            record_one("\r", "Return", 480)
        elif char == " ":
            record_one(" ", "space", 190)
        elif char in punctuations:
            record_one(char, char, 240)
        else:
            record_one(char, char, rhythm[index % len(rhythm)])
        if index in (18, 47):   # 打错两个字，当场退格改回来
            record_one("", "BackSpace", 45 + index % 17)
            record_one("", "BackSpace", 38 + index % 13)

    profile = habits.analyze_events(events)
    check("习惯：合成按键事件能算出 profile",
          isinstance(profile, dict) and int(profile.get("sample_chars") or 0) >= 40,
          "样本 %s 字" % (profile.get("sample_chars") if isinstance(profile, dict) else profile))
    expected = habits.suggest(profile, dict(cfg.section("timing")), dict(cfg.section("human")))
    check("习惯：能给出建议参数（含每字间隔）", "timing.char_delay_ms" in expected,
          str(sorted(expected)))
    window._apply_habits_profile(profile)
    check("习惯：套用后每字间隔等于建议值",
          int(cfg.get("timing.char_delay_ms")) == int(expected["timing.char_delay_ms"]),
          "%s vs %s" % (cfg.get("timing.char_delay_ms"), expected.get("timing.char_delay_ms")))
    check("习惯：套用后抖动等于建议值",
          int(cfg.get("timing.char_delay_jitter_pct")) == int(expected["timing.char_delay_jitter_pct"]),
          "%s vs %s" % (cfg.get("timing.char_delay_jitter_pct"),
                        expected.get("timing.char_delay_jitter_pct")))
    check("习惯：套用后快慢交替开关同步",
          bool(cfg.get("timing.burst_mode")) == bool(expected.get("timing.burst_mode", True)),
          str(cfg.get("timing.burst_mode")))
    calibrated_at = str(cfg.get("habits.calibrated_at") or "").strip()
    check("习惯：套用后写入校准时间", bool(calibrated_at), calibrated_at)
    check("习惯：套用的参数记录进配置",
          isinstance(cfg.get("habits.applied"), dict) and bool(cfg.get("habits.applied")),
          str(cfg.get("habits.applied")))
    applied_state = window.habits_status_var.get()
    check("习惯：状态行变成“已校准”", "已校准" in applied_state, applied_state)
    check("习惯：状态行带中位间隔与字/分",
          "中位间隔" in applied_state and "字/分" in applied_state, applied_state)
    if "human.typo_rate" in expected:
        typo_gui_var = window._vars[("human.typo_rate", "float")]
        want_typo = round(float(expected["human.typo_rate"]) * 100.0, 3)
        got_typo = float(typo_gui_var.get())
        check("习惯：错字率百分比变量同步（×100）", abs(got_typo - want_typo) < 0.001,
              "界面=%s 期望=%s 配置=%s" % (got_typo, want_typo, cfg.get("human.typo_rate")))
    else:
        check("习惯：错字率百分比变量同步（×100）", False, "suggest 没有返回 human.typo_rate")

    # ---- 采集对话框：能打开、有输入框、关闭后清理干净（不触发“完成并套用”弹窗）----
    window._open_habits()
    window.root.update()
    habit_dialog = getattr(window, "_habit_window", None)
    habit_input = getattr(window, "_habit_input", None)
    check("习惯：采集对话框能打开",
          habit_dialog is not None and bool(habit_dialog.winfo_exists()))
    check("习惯：对话框里有可输入的打字区",
          habit_input is not None and bool(habit_input.winfo_exists()))
    check("习惯：对话框里显示实时统计行",
          bool(getattr(window, "_habit_status_var", None))
          and bool(window._habit_status_var.get()))
    window._close_habits()
    check("习惯：关闭对话框后清理干净（_habit_window 为 None）",
          getattr(window, "_habit_window", None) is None)

    root.destroy()
    failed = [name for name, ok in RESULTS if not ok]
    print("-" * 68)
    print("设置界面测试：共 %d 项，失败 %d 项" % (len(RESULTS), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
