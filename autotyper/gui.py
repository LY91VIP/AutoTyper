# -*- coding: utf-8 -*-
"""设置界面（ttk）：楼层编辑、速度拟人、游戏式改键、演示模式、自动保存。"""

from __future__ import annotations

import os
import threading
import time
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk

from . import paths
from .config import DEFAULTS
from .win32_input import name_to_vk, vk_to_label

BG = "#f4f6f8"
CARD = "#ffffff"
INK = "#1f2937"
MUTED = "#6b7280"
ACCENT = "#2563eb"
ACCENT_DARK = "#1d4ed8"
BORDER = "#d7dbe0"
HEADER_BG = "#e9eef5"
SEL_BG = "#dbeafe"
GOOD = "#047857"
BAD = "#c2410c"

PERCENT_KEYS = {
    "human.typo_rate", "human.transpose_rate", "human.double_char_rate",
    "human.late_notice_rate", "human.skip_fix_rate",
}
ACTION_NAMES = {"start": "开始 / 继续", "stop": "停止", "panic": "紧急停止"}


def pretty_key(name: str) -> str:
    if not name:
        return "（未设置）"
    return vk_to_label(name_to_vk(name))


def setup_look(root: tk.Tk) -> None:
    try:
        root.option_add("*Font", ("Microsoft YaHei UI", 10))
    except Exception:
        pass
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    base = ("Microsoft YaHei UI", 10)
    for name in ("TLabel", "TButton", "TCheckbutton", "TRadiobutton", "TEntry", "TCombobox"):
        try:
            style.configure(name, font=base)
        except Exception:
            pass
    try:
        family = "Microsoft YaHei UI"
        line_space = tkfont.Font(family, size=10).metrics("linespace")
        row_height = max(30, int(line_space) + 14)
        style.configure("Treeview", font=base, rowheight=row_height, background=CARD,
                        fieldbackground=CARD, foreground=INK, borderwidth=0)
        style.configure("Treeview.Heading", font=(family, 10, "bold"), background=HEADER_BG,
                        foreground=INK, relief="flat")
        style.map("Treeview", background=[("selected", SEL_BG)],
                  foreground=[("selected", INK)])
    except Exception:
        style.configure("Treeview", rowheight=32)
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=CARD)
    style.configure("TLabel", background=BG, foreground=INK)
    style.configure("Card.TLabel", background=CARD, foreground=INK)
    style.configure("CardTitle.TLabel", background=CARD, foreground=ACCENT_DARK,
                    font=("Microsoft YaHei UI", 10, "bold"))
    style.configure("CardMuted.TLabel", background=CARD, foreground=MUTED)
    style.configure("CardGood.TLabel", background=CARD, foreground=GOOD)
    style.configure("CardBad.TLabel", background=CARD, foreground=BAD)
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", padding=(14, 6), font=("Microsoft YaHei UI", 10))
    style.configure("Accent.TButton", font=("Microsoft YaHei UI", 10, "bold"),
                    foreground="#ffffff", background=ACCENT)
    style.map("Accent.TButton",
              background=[("active", ACCENT_DARK), ("pressed", ACCENT_DARK)])
    style.configure("TLabelFrame", background=CARD, foreground=INK)
    style.configure("TLabelframe.Label", background=CARD, foreground=ACCENT_DARK,
                    font=("Microsoft YaHei UI", 10, "bold"))


class SettingsWindow:
    def __init__(self, app, root: tk.Tk):
        self.app = app
        self.root = root
        self.cfg = app.cfg
        self._vars = {}
        self._binding_vars = {}
        self._capture_action = None
        self._autosave_job = None
        self._step_autosave_job = None
        self._autosave_ready = False
        self._demo_window = None
        self._demo_stop = threading.Event()
        self._demo_after = None
        self._selected_index = 0
        self.steps = [dict(item) for item in self.cfg.steps()]
        if not self.steps:
            self.steps = [dict(item) for item in DEFAULTS["steps"]]

        setup_look(root)
        root.title("打字助手 · 设置")
        try:
            # 防止窗口被拖成窄条，导致内容挤断看不清
            root.minsize(880, 620)
        except Exception:
            pass
        root.configure(bg=BG)
        self._build()
        self._load_profiles()
        self.refresh_steps_view()
        self.refresh_percent_fields()
        self.refresh_hotkey_live()
        self.refresh_lock_state()
        for var in (self.mode_var, self.script_var):
            try:
                var.trace_add("write", lambda *_a: self._schedule_autosave())
            except Exception:
                pass
        self._autosave_ready = True
        close_handler = getattr(self.app, "_on_close_button", None) or getattr(self.app, "hide_window", None)
        if close_handler is not None:
            try:
                self.root.protocol("WM_DELETE_WINDOW", close_handler)
            except Exception:
                pass

    # ------------------------------------------------------------ 构建
    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)

        status = ttk.Frame(outer, style="Card.TFrame", padding=12)
        status.pack(fill="x", pady=(0, 8))
        self.status_var = tk.StringVar(value="待命")
        ttk.Label(status, textvariable=self.status_var, style="CardTitle.TLabel").pack(anchor="w")
        self.detail_var = tk.StringVar(value="已打 0 / 0 个字符")
        ttk.Label(status, textvariable=self.detail_var, style="CardMuted.TLabel").pack(anchor="w", pady=(2, 0))
        self.hint_var = tk.StringVar(value="")
        ttk.Label(status, textvariable=self.hint_var, style="CardBad.TLabel").pack(anchor="w", pady=(2, 0))
        self.hotkey_live_var = tk.StringVar(value="热键未生效")
        ttk.Label(status, textvariable=self.hotkey_live_var, style="CardGood.TLabel").pack(anchor="w", pady=(2, 0))
        self.saved_var = tk.StringVar(value="改动会自动保存")
        ttk.Label(status, textvariable=self.saved_var, style="CardMuted.TLabel").pack(anchor="w", pady=(2, 0))

        bar = ttk.Frame(outer)
        bar.pack(side="bottom", fill="x", pady=(8, 0))
        ttk.Button(bar, text="💾 保存并应用", style="Accent.TButton",
                   command=lambda: self.app.apply_from_ui()).pack(side="left")
        ttk.Button(bar, text="试打几个字", command=self.app.test_type).pack(side="left", padx=6)
        ttk.Button(bar, text="恢复默认设置", command=self.restore_defaults).pack(side="left", padx=6)
        ttk.Label(bar, text="（点右上角 × 也是退出程序）", foreground=MUTED).pack(side="right", padx=(0, 8))
        self.quit_button = ttk.Button(bar, text="✖ 退出程序", command=self.app.quit_app)
        self.quit_button.pack(side="right")
        self.hide_button = ttk.Button(bar, text="隐藏到托盘（不退出）", command=self.app.hide_window)
        self.hide_button.pack(side="right", padx=6)

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True, pady=8)
        self._build_steps_tab(notebook)
        self._build_timing_tab(notebook)
        self._build_human_tab(notebook)
        self._build_keys_tab(notebook)
        self._build_more_tab(notebook)

    # ---------------- ① 打什么 ----------------
    def _build_steps_tab(self, notebook) -> None:
        tab = ttk.Frame(notebook, padding=10)
        notebook.add(tab, text="① 打什么")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)

        # 上下可拖动分隔：往上拖楼层框变大，往下拖编辑框变大（会记住位置）
        self.steps_paned = ttk.Panedwindow(tab, orient="vertical")
        self.steps_paned.grid(row=0, column=0, sticky="nsew")

        top = ttk.Frame(self.steps_paned, padding=(0, 0, 0, 4))
        top.columnconfigure(0, weight=1)
        top.rowconfigure(1, weight=1)
        self.steps_paned.add(top, weight=1)

        ttk.Label(top, text="像盖房子一样，一层一层填：按一次 ↓ 打一层（中间的分隔条可以上下拖，把楼层框拉大）。",
                  foreground=MUTED).grid(row=0, column=0, columnspan=2, sticky="w")

        self.step_tree = ttk.Treeview(top, columns=("name", "preview"), show="headings",
                                      height=6, selectmode="browse")
        self.step_tree.heading("name", text="楼层（点一下选中）")
        self.step_tree.heading("preview", text="内容预览")
        self.step_tree.column("name", width=210, anchor="w")
        self.step_tree.column("preview", width=420, anchor="w")
        self.step_tree.grid(row=1, column=0, sticky="nsew", pady=6)
        tree_scroll = ttk.Scrollbar(top, orient="vertical", command=self.step_tree.yview)
        self.step_tree.configure(yscrollcommand=tree_scroll.set)
        tree_scroll.grid(row=1, column=1, sticky="ns", pady=6)
        self.step_tree.bind("<<TreeviewSelect>>", self._on_step_select)

        buttons = ttk.Frame(top)
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=2)
        ttk.Button(buttons, text="➕ 新增一层", command=self.add_step).pack(side="left")
        ttk.Button(buttons, text="⬆ 上移", command=lambda: self.move_step(-1)).pack(side="left", padx=4)
        ttk.Button(buttons, text="⬇ 下移", command=lambda: self.move_step(1)).pack(side="left", padx=4)
        ttk.Button(buttons, text="🗑 删除（立即生效）", command=self.delete_step).pack(side="left", padx=4)
        ttk.Button(buttons, text="📄 从文件导入", command=self.import_steps_from_file).pack(side="right")

        bottom = ttk.Frame(self.steps_paned)
        bottom.columnconfigure(0, weight=1)
        bottom.rowconfigure(2, weight=1)
        self.steps_paned.add(bottom, weight=2)

        name_row = ttk.Frame(bottom)
        name_row.grid(row=0, column=0, sticky="ew", pady=(4, 0))
        ttk.Label(name_row, text="这一层的名字：").pack(side="left")
        self.step_name_var = tk.StringVar()
        ttk.Entry(name_row, textvariable=self.step_name_var, width=28).pack(side="left", padx=6)
        self.mode_var = tk.StringVar(value=str(self.cfg.get("source.mode", "steps")))
        self.script_var = tk.StringVar(value=str(self.cfg.get("source.script", "")))

        ttk.Label(bottom, text="这一层要打的代码（原样打，缩进会自动处理）：",
                  foreground=MUTED).grid(row=1, column=0, sticky="w", pady=(6, 0))
        editor_box = ttk.Frame(bottom, style="Card.TFrame")
        editor_box.grid(row=2, column=0, sticky="nsew")
        self.step_text = tk.Text(editor_box, height=5, wrap="none", font=("Consolas", 11),
                                 undo=True, bg="#fbfcfe", relief="flat", insertbackground=INK)
        scroll = ttk.Scrollbar(editor_box, orient="vertical", command=self.step_text.yview)
        self.step_text.configure(yscrollcommand=scroll.set)
        self.step_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.step_text.bind("<Tab>", self._insert_tab)
        self.step_text.bind("<Control-Return>", lambda _e: self.save_current_step())
        self.step_text.bind("<KeyRelease>", lambda _e: self._schedule_step_autosave())

        bottom_row = ttk.Frame(bottom)
        bottom_row.grid(row=3, column=0, sticky="ew", pady=8)
        ttk.Button(bottom_row, text="💾 保存这一层（立即生效）",
                   command=self.save_current_step).pack(side="left")
        ttk.Button(bottom_row, text="▶ 预览打字效果", command=self._show_demo).pack(side="left", padx=6)
        ttk.Button(bottom_row, text="重置进度（从头开始）",
                   command=self.app.reset_progress).pack(side="left", padx=6)
        ttk.Button(bottom_row, text="干跑预览（不按键）", command=self.show_dry_run).pack(side="left", padx=6)
        self.steps_summary_var = tk.StringVar(value="")
        ttk.Label(bottom_row, textvariable=self.steps_summary_var, foreground=MUTED).pack(side="left", padx=10)
        ttk.Checkbutton(bottom_row, text="每层打完自动按回车（换行）",
                        variable=self._var("source.auto_enter", "bool"),
                        command=self.apply_steps_now).pack(side="right")

    # ---------------- 分隔条位置 ----------------
    def _restore_paned(self) -> None:
        try:
            ratio = float(self.cfg.get("ui.paned_ratio", 0.42) or 0.42)
        except (TypeError, ValueError):
            ratio = 0.42
        ratio = min(0.85, max(0.12, ratio))
        try:
            self.steps_paned.bind("<ButtonRelease-1>", lambda _e: self._save_paned())
        except Exception:
            pass
        try:
            height = int(self.steps_paned.winfo_height())
            if height > 120:
                self.steps_paned.sashpos(0, int(height * ratio))
        except Exception:
            pass

    def _save_paned(self) -> None:
        try:
            height = max(1, int(self.steps_paned.winfo_height()))
            position = int(self.steps_paned.sashpos(0))
            ratio = min(0.85, max(0.12, position / float(height)))
            self.cfg.set("ui.paned_ratio", round(ratio, 3))
            self.cfg.save()
        except Exception:
            pass

    # ---------------- ② 多快 ----------------
    def _build_timing_tab(self, notebook) -> None:
        tab = ttk.Frame(notebook, padding=12)
        notebook.add(tab, text="② 多快")
        self._spin(tab, 0, "开始前等多久（毫秒）", "timing.start_delay_ms", 0, 5000, 50)
        self._spin(tab, 1, "每个字的间隔（毫秒）", "timing.char_delay_ms", 1, 1000, 5)
        self._spin(tab, 2, "间隔抖动（百分点）", "timing.char_delay_jitter_pct", 0, 100, 5)
        self._spin(tab, 3, "换行后停顿（毫秒）", "timing.newline_pause_ms", 0, 3000, 20)
        self._spin(tab, 4, "标点后停顿（毫秒）", "timing.punctuation_pause_ms", 0, 3000, 20)
        self._check(tab, 5, "快慢交替（更像真人）", "timing.burst_mode")
        ttk.Label(tab, text="提示：间隔 50~80 毫秒最像真人；太快会被编辑器当粘贴。",
                  foreground=MUTED).grid(row=6, column=0, columnspan=2, sticky="w", pady=(10, 0))

    # ---------------- ③ 像不像真人 ----------------
    def _build_human_tab(self, notebook) -> None:
        tab = ttk.Frame(notebook, padding=12)
        notebook.add(tab, text="③ 像不像真人")
        self._spin(tab, 0, "打错字的频率（%）", "human.typo_rate", 0, 100, 10, percent=True)
        self._spin(tab, 1, "相邻两个字打反（%）", "human.transpose_rate", 0, 100, 10, percent=True)
        self._spin(tab, 2, "同一个字连击（%）", "human.double_char_rate", 0, 100, 10, percent=True)
        self._spin(tab, 3, "发现错误前多打几个字（%）", "human.late_notice_rate", 0, 100, 10, percent=True)
        self._spin(tab, 4, "回头改错前愣多久最小（毫秒）", "human.fix_delay_min_ms", 0, 3000, 20)
        self._spin(tab, 5, "回头改错前愣多久最大（毫秒）", "human.fix_delay_max_ms", 0, 3000, 20)
        ttk.Label(tab, text="错字率 2%~4% 最自然；0 就是完全不打错。",
                  foreground=MUTED).grid(row=6, column=0, columnspan=2, sticky="w", pady=(10, 0))

    # ---------------- ④ 控制按键 ----------------
    def _build_keys_tab(self, notebook) -> None:
        tab = ttk.Frame(notebook, padding=12)
        notebook.add(tab, text="④ 控制按键")
        ttk.Label(tab, text="像游戏改键一样：点一下“按下新键”，然后直接按你想用的键。",
                  foreground=MUTED).grid(row=0, column=0, columnspan=2, sticky="w")
        self._key_entries = {}
        rows = (("start", "开始 / 继续（↓）"), ("stop", "停止（↑）"), ("panic", "紧急停止（F12）"))
        for index, (action, label) in enumerate(rows, start=1):
            row = ttk.Frame(tab)
            row.grid(row=index, column=0, columnspan=2, sticky="ew", pady=3)
            ttk.Label(row, text=label, width=22).pack(side="left")
            var = tk.StringVar(value=str(self.cfg.get("hotkeys.%s_key" % action, "") or ""))
            self._binding_vars[action] = var
            entry = ttk.Entry(row, textvariable=var, width=12, justify="center")
            entry.pack(side="left", padx=6)
            entry.bind("<KeyPress>", lambda event, act=action: self._on_binding_key(event, act))
            self._key_entries[action] = entry
            ttk.Button(row, text="按下新键",
                       command=lambda act=action: self._begin_capture(act)).pack(side="left")
        ttk.Button(tab, text="恢复默认按键", command=self.reset_keys).grid(
            row=5, column=0, sticky="w", pady=(8, 0))
        self._combo(tab, 6, "↓/↑ 要不要拦下来", "hotkeys.swallow_mode",
                    [("target", "只在我下面指定的程序里拦（推荐）"),
                     ("always", "所有程序里都拦"),
                     ("off", "不拦（哪都能用 ↓/↑）")],
                    "拦下来 = 目标程序收不到这几个键")
        self._entry(tab, 7, "目标程序的进程名（如 pycharm64.exe）", "hotkeys.target_process")
        ttk.Button(tab, text="读取当前前台程序", command=self._grab_foreground_process).grid(
            row=8, column=1, sticky="w")
        self._spin(tab, 9, "按一次最多打多少字（0=不限）", "safety.max_chars_per_trigger", 0, 1000000, 100)

        lock_box = ttk.LabelFrame(tab, text="演示模式（防止演示时误碰键盘）", padding=8)
        lock_box.grid(row=10, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        lock_row = ttk.Frame(lock_box)
        lock_row.pack(fill="x")
        self.lock_var = tk.BooleanVar(value=False)
        self.lock_check = ttk.Checkbutton(lock_row, text="锁住 26 个字母键（只保留 ↓/↑ 控制）",
                                          variable=self.lock_var, command=self._on_lock_toggled)
        self.lock_check.pack(side="left")
        self.lock_state_var = tk.StringVar(value="")
        ttk.Label(lock_row, textvariable=self.lock_state_var, foreground=MUTED).pack(side="left", padx=10)
        ttk.Label(lock_box, justify="left",
                  text=("开启后：物理字母键打不进任何程序，↓/↑ 仍可用，鼠标全程正常。\n"
                        "解锁方式（任选其一）：按住 Esc 2 秒 · 右键托盘菜单 · 退出程序。"),
                  foreground=BAD).pack(anchor="w", pady=(4, 0))

    # ---------------- ⑤ 更多 ----------------
    def _build_more_tab(self, notebook) -> None:
        tab = ttk.Frame(notebook, padding=12)
        notebook.add(tab, text="⑤ 更多")
        box = ttk.LabelFrame(tab, text="运行方式", padding=8)
        box.pack(fill="x", pady=(0, 10))
        self._check(box, 1, "点 × 时只收进托盘（不退出程序）", "behavior.close_to_tray",
                    "默认不勾：点 × 直接退出程序；想后台待命请点底部「隐藏到托盘」")
        self._check(box, 2, "在任务栏显示设置窗口", "behavior.show_in_taskbar")
        self._check(box, 3, "每层打完提示音", "behavior.sound_on_done")
        self._check(box, 4, "打字时窗口被切走就自动停", "safety.pause_on_focus_change")
        self._check(box, 7, "开始打字前自动松开 Shift/Ctrl/Alt/Win", "safety.release_modifiers",
                    "防止你手还压着 Ctrl 时按 ↓，打出来的字变成快捷键")
        self._check(box, 5, "开机自动启动", "behavior.autostart")
        self._combo(box, 6, "打字方式", "behavior.input_mode",
                    [("auto", "自动：ASCII 用真实按键（大小写最准）"),
                     ("unicode", "全部用字符注入（兼容性优先）")],
                    "自动模式会自动判断大小写：大写字母自动按 Shift")

        tools = ttk.LabelFrame(tab, text="脚本 / 工具", padding=8)
        tools.pack(fill="x")
        ttk.Label(tools, text="当前配置文件：%s" % paths.describe_path(self.cfg.path),
                  foreground=MUTED).pack(anchor="w")
        btns = ttk.Frame(tools)
        btns.pack(fill="x", pady=6)
        ttk.Button(btns, text="打开配置文件所在目录", command=self._open_config_dir).pack(side="left")
        ttk.Button(btns, text="重新载入脚本", command=self.app.reload_script).pack(side="left", padx=6)
        ttk.Button(btns, text="恢复默认设置", command=self.restore_defaults).pack(side="left", padx=6)

    # ------------------------------------------------------------ 控件工具
    def _var(self, dotted: str, kind: str):
        key = (dotted, kind)
        if key in self._vars:
            return self._vars[key]
        value = self.cfg.get(dotted)
        if kind == "bool":
            var = tk.BooleanVar(value=bool(value))
        elif kind == "int":
            var = tk.IntVar(value=int(value or 0))
        elif kind == "float":
            try:
                number = float(value or 0)
            except (TypeError, ValueError):
                number = 0.0
            if dotted in PERCENT_KEYS:
                number *= 100.0
            var = tk.StringVar(value=("%g" % number))
        else:
            var = tk.StringVar(value="" if value is None else str(value))
        self._vars[key] = var
        try:
            var.trace_add("write", lambda *_a: self._schedule_autosave())
        except Exception:
            pass
        return var

    def _spin(self, parent, row, label, dotted, low, high, step, percent=False):
        kind = "float" if percent else "int"
        var = self._var(dotted, kind)
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=0, columnspan=2, sticky="w", pady=3)
        ttk.Label(frame, text=label, width=30).pack(side="left")
        ttk.Spinbox(frame, from_=low, to=high, increment=step, textvariable=var,
                    width=10).pack(side="left")
        return var

    def _check(self, parent, row, label, dotted, tip=""):
        var = self._var(dotted, "bool")
        holder = ttk.Frame(parent)
        holder.grid(row=row, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Checkbutton(holder, text=label, variable=var).pack(side="left")
        if tip:
            ttk.Label(holder, text=tip, foreground=MUTED).pack(side="left", padx=8)
        return var

    def _combo(self, parent, row, label, dotted, choices, tip=""):
        var = self._var(dotted, "str")
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=3)
        ttk.Label(frame, text=label, width=24).pack(side="left")
        labels = [item[1] for item in choices]
        values = [item[0] for item in choices]
        combo = ttk.Combobox(frame, values=labels, state="readonly", width=34)
        combo.pack(side="left")

        def sync_from_var(*_a):
            try:
                current = str(var.get())
                combo.current(values.index(current) if current in values else 0)
            except Exception:
                pass

        def on_choice(_event=None):
            index = combo.current()
            if 0 <= index < len(values):
                var.set(values[index])

        combo.bind("<<ComboboxSelected>>", on_choice)
        var.trace_add("write", sync_from_var)
        sync_from_var()
        if tip:
            ttk.Label(frame, text=tip, foreground=MUTED).pack(side="left", padx=8)
        return var

    def _entry(self, parent, row, label, dotted, width=32):
        var = self._var(dotted, "str")
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=3)
        ttk.Label(frame, text=label, width=24).pack(side="left")
        ttk.Entry(frame, textvariable=var, width=width).pack(side="left")
        return var

    # ------------------------------------------------------------ 楼层
    def _selected_step_index(self):
        selection = self.step_tree.selection() if hasattr(self, "step_tree") else ()
        if selection:
            try:
                return int(selection[0])
            except Exception:
                return 0
        return self._selected_index

    def _on_step_select(self, _event=None) -> None:
        self._selected_index = self._selected_step_index()
        self._load_step_editor(self._selected_index, select=False)

    def _load_step_editor(self, index: int, select: bool = False) -> None:
        if not (0 <= index < len(self.steps)):
            return
        step = self.steps[index]
        self.step_name_var.set(str(step.get("name") or ""))
        self.step_text.delete("1.0", "end")
        self.step_text.insert("1.0", str(step.get("text") or ""))
        if select and index < len(self.step_tree.get_children()):
            try:
                self.step_tree.selection_set(str(index))
            except Exception:
                pass

    def refresh_steps_view(self, preferred: int = None) -> None:
        if not hasattr(self, "step_tree"):
            return
        self.step_tree.delete(*self.step_tree.get_children())
        for index, step in enumerate(self.steps):
            text = str(step.get("text") or "")
            preview = text.replace("\n", " ⏎ ")[:64]
            self.step_tree.insert("", "end", iid=str(index),
                                  values=(step.get("name") or ("第 %d 层" % (index + 1)), preview))
        if not self.steps:
            return
        index = self._selected_index if preferred is None else preferred
        index = max(0, min(index, len(self.steps) - 1))
        self._selected_index = index
        if str(index) in self.step_tree.get_children():
            self.step_tree.selection_set(str(index))
        self._load_step_editor(index, select=False)
        total = sum(len(str(item.get("text") or "")) for item in self.steps)
        self.steps_summary_var.set("共 %d 层，%d 个字符" % (len(self.steps), total))
        self._update_sync_warning()

    def _apply_editor(self) -> None:
        if not self.steps:
            return
        index = self._selected_index
        if not (0 <= index < len(self.steps)):
            return
        text = self.step_text.get("1.0", "end-1c")
        name = self.step_name_var.get().strip() or ("第 %d 层" % (index + 1))
        if self.steps[index].get("text") == text and self.steps[index].get("name") == name:
            return
        self.steps[index] = {"name": name, "text": text}

    def apply_steps_now(self) -> None:
        self._apply_editor()
        self.collect()
        self.cfg.set_steps(self.steps)
        self.cfg.set("source.mode", "steps")
        self.cfg.save()
        try:
            self.app.engine.reload_text(keep_position=True)
        except Exception:
            pass
        self.refresh_steps_view(preferred=self._selected_index)
        self.hint_var.set("已保存（立即生效）")

    def add_step(self) -> None:
        self._apply_editor()
        self.steps.append({"name": "第 %d 层" % (len(self.steps) + 1), "text": ""})
        self._selected_index = len(self.steps) - 1
        self.refresh_steps_view(preferred=self._selected_index)
        self._load_step_editor(self._selected_index, select=True)
        self.apply_steps_now()

    def delete_step(self) -> None:
        if len(self.steps) <= 1:
            self.hint_var.set("至少要留一层")
            return
        index = self._selected_index
        if not (0 <= index < len(self.steps)):
            index = 0
        self.steps.pop(index)
        self._selected_index = max(0, min(index, len(self.steps) - 1))
        self.refresh_steps_view(preferred=self._selected_index)
        self.apply_steps_now()

    def move_step(self, delta: int) -> None:
        self._apply_editor()
        index = self._selected_index
        target = index + delta
        if not (0 <= index < len(self.steps) and 0 <= target < len(self.steps)):
            return
        self.steps[index], self.steps[target] = self.steps[target], self.steps[index]
        self._selected_index = target
        self.refresh_steps_view(preferred=target)
        self._load_step_editor(target, select=True)
        self.apply_steps_now()

    def save_current_step(self) -> None:
        self.apply_steps_now()

    def import_steps_from_file(self) -> None:
        path = filedialog.askopenfilename(title="选择代码文件",
                                          filetypes=[("文本文件", "*.txt *.py"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8-sig") as handle:
                lines = handle.read().split("\n")
        except Exception as exc:
            messagebox.showerror("读不了这个文件", str(exc))
            return
        added = 0
        for line in lines:
            if not line.strip():
                continue
            self.steps.append({"name": os.path.basename(path), "text": line})
            added += 1
            if added >= 200:
                break
        self.refresh_steps_view()
        self.apply_steps_now()
        self.hint_var.set("已从文件导入 %d 层" % added)

    def _insert_tab(self, _event=None):
        self.step_text.insert("insert", "    ")
        return "break"

    def _update_sync_warning(self) -> None:
        pass

    # ------------------------------------------------------------ 百分比/采集
    def refresh_percent_fields(self) -> None:
        for (dotted, kind), var in list(self._vars.items()):
            if dotted in PERCENT_KEYS:
                try:
                    var.set(round(float(self.cfg.get(dotted) or 0) * 100.0, 3))
                except Exception:
                    pass

    def collect(self) -> None:
        for (dotted, kind), var in list(self._vars.items()):
            try:
                raw = var.get()
            except Exception:
                continue
            try:
                if kind == "bool":
                    value = bool(raw)
                elif kind == "int":
                    value = int(float(raw))
                elif kind == "float":
                    value = float(raw)
                    if dotted in PERCENT_KEYS:
                        value /= 100.0
                else:
                    value = str(raw)
                self.cfg.set(dotted, value)
            except Exception:
                continue
        self.collect_hotkeys()
        self.cfg.set_steps(self.steps)
        self.cfg.set("source.script", self.script_var.get().strip())
        self.cfg.set("source.mode", self.mode_var.get() or "steps")

    def collect_hotkeys(self) -> None:
        for action, var in self._binding_vars.items():
            try:
                self.cfg.set("hotkeys.%s_key" % action, str(var.get() or "").strip().lower())
            except Exception:
                pass

    def _load_profiles(self) -> None:
        pass

    def collect_profiles(self) -> dict:
        return {}

    # ------------------------------------------------------------ 自动保存
    def _schedule_autosave(self) -> None:
        if not self._autosave_ready:
            return
        try:
            if self._autosave_job is not None:
                self.root.after_cancel(self._autosave_job)
        except Exception:
            pass
        try:
            self._autosave_job = self.root.after(600, self._autosave_now)
        except Exception:
            self._autosave_job = None

    def _autosave_now(self) -> None:
        self._autosave_job = None
        try:
            self.collect()
            self.cfg.save()
            self.saved_var.set("已自动保存 " + time.strftime("%H:%M:%S"))
        except Exception as exc:
            try:
                self.saved_var.set("自动保存失败：%s" % exc)
            except Exception:
                pass

    def _schedule_step_autosave(self) -> None:
        if not self._autosave_ready:
            return
        try:
            if self._step_autosave_job is not None:
                self.root.after_cancel(self._step_autosave_job)
        except Exception:
            pass
        try:
            self._step_autosave_job = self.root.after(1200, self._step_autosave_now)
        except Exception:
            self._step_autosave_job = None

    def _step_autosave_now(self) -> None:
        self._step_autosave_job = None
        try:
            self.save_current_step()
            self.saved_var.set("已自动保存 " + time.strftime("%H:%M:%S"))
        except Exception:
            pass

    # ------------------------------------------------------------ 改键
    def _on_binding_key(self, event, action):
        if self._capture_action != action:
            return None
        self._finish_capture(event.keysym)
        return "break"

    def _begin_capture(self, action: str) -> None:
        self._capture_action = action
        entry = self._key_entries.get(action)
        if entry is not None:
            entry.configure(foreground="#c00")
        self.hint_var.set("请直接按下你想用的键……（Esc 取消）")

    def _finish_capture(self, keysym: str) -> None:
        action = self._capture_action
        self._capture_action = None
        entry = self._key_entries.get(action) if action else None
        if entry is not None:
            entry.configure(foreground=INK)
        if not action:
            return
        if keysym in ("Escape",):
            self.hint_var.set("已取消")
            return
        name = self._keysym_to_name(keysym)
        if not name:
            self.hint_var.set("这个键不能单独用，换一个（比如 F8、方向键、字母）")
            return
        self._set_binding(action, name)

    @staticmethod
    def _keysym_to_name(keysym: str) -> str:
        mapping = {"Down": "down", "Up": "up", "Left": "left", "Right": "right",
                   "Return": "enter", "space": "space", "Tab": "tab",
                   "BackSpace": "backspace", "Control_L": "", "Control_R": "",
                   "Shift_L": "", "Shift_R": "", "Alt_L": "", "Alt_R": "",
                   "Super_L": "", "Super_R": "", "Escape": "esc"}
        if keysym in mapping:
            return mapping[keysym]
        if keysym.startswith("F") and keysym[1:].isdigit():
            return keysym.lower()
        if len(keysym) == 1 and keysym.isprintable():
            return keysym.lower()
        return ""

    def _set_binding(self, action: str, key: str) -> None:
        key = (key or "").strip().lower()
        cleared = []
        if key:
            for other, var in self._binding_vars.items():
                if other != action and str(var.get() or "").strip().lower() == key:
                    var.set("")
                    cleared.append(ACTION_NAMES.get(other, other))
        self._binding_vars[action].set(key)
        if cleared:
            self.hint_var.set("已把「%s」改成 %s；同一个键不能绑两个动作，已自动清掉：%s（立刻生效）"
                              % (ACTION_NAMES.get(action, action), pretty_key(key), "、".join(cleared)))
        else:
            self.hint_var.set("已把「%s」改成 %s（立刻生效，旧按键已解绑）"
                              % (ACTION_NAMES.get(action, action), pretty_key(key)))
        try:
            self.app.apply_hotkeys_live()
        except Exception:
            pass

    def reset_keys(self) -> None:
        defaults = {"start": "down", "stop": "up", "panic": "f12"}
        for action, var in self._binding_vars.items():
            var.set(defaults.get(action, ""))
        self.hint_var.set("已恢复默认按键（立刻生效）")
        try:
            self.app.apply_hotkeys_live()
        except Exception:
            pass

    def refresh_hotkey_live(self) -> None:
        parts = ["%s = %s" % (ACTION_NAMES.get(action, action),
                              pretty_key(str(var.get() or "")))
                 for action, var in self._binding_vars.items()]
        self.hotkey_live_var.set("现在真正生效：" + "   ".join(parts))

    def _grab_foreground_process(self) -> None:
        try:
            from .win32_input import foreground_info
            info = foreground_info()
            name = str(info.get("process") or "")
            if name:
                self._var("hotkeys.target_process", "str").set(name)
                self.hint_var.set("已填上当前前台程序：%s" % name)
        except Exception:
            pass

    # ------------------------------------------------------------ 演示模式
    def _on_lock_toggled(self) -> None:
        try:
            self.app.toggle_key_lock(bool(self.lock_var.get()))
        except Exception:
            pass
        self.refresh_lock_state()

    def refresh_lock_state(self) -> None:
        locked = bool(getattr(getattr(self.app, "hotkeys", None), "key_locked", False))
        try:
            self.lock_var.set(locked)
            self.lock_state_var.set("现在：已锁住字母键" if locked else "现在：未锁定")
        except Exception:
            pass

    # ------------------------------------------------------------ 预览/演示
    def show_dry_run(self) -> None:
        try:
            lines = self.app.engine.preview(1)
        except Exception as exc:
            lines = ["预览失败：%s" % exc]
        messagebox.showinfo("干跑预览（不会真的按键）", "\n".join(lines))

    def restore_defaults(self) -> None:
        if not messagebox.askyesno("恢复默认设置", "会把所有设置恢复成默认值（包括楼层内容）。确定吗？"):
            return
        self.cfg.data = __import__("copy").deepcopy(DEFAULTS)
        self.steps = [dict(item) for item in self.cfg.steps()]
        for (dotted, kind), var in list(self._vars.items()):
            value = self.cfg.get(dotted)
            try:
                if kind == "bool":
                    var.set(bool(value))
                elif kind == "int":
                    var.set(int(value or 0))
                elif kind == "float":
                    var.set(float(value or 0) * (100.0 if dotted in PERCENT_KEYS else 1.0))
                else:
                    var.set("" if value is None else str(value))
            except Exception:
                pass
        self.collect()
        self.cfg.save()
        self.refresh_steps_view()
        self.hint_var.set("已恢复默认设置")

    def _open_config_dir(self) -> None:
        try:
            os.startfile(os.path.dirname(self.cfg.path) or ".")
        except Exception:
            pass

    def _show_demo(self) -> None:
        try:
            info = self.app.engine.next_segment_text()
        except Exception:
            info = None
        if not info:
            messagebox.showinfo("没有下一层了", "已经全部打完了，想重来请点“重置进度”。")
            return
        if self._demo_window is not None:
            self._close_demo()
        window = tk.Toplevel(self.root)
        window.title("打字效果预览（不会真的按键）")
        window.configure(bg=BG)
        view = tk.Text(window, width=80, height=14, font=("Consolas", 11),
                       bg="#fbfcfe", relief="flat")
        view.pack(fill="both", expand=True, padx=10, pady=(10, 0))
        tip = tk.StringVar(value="正在演示：%s" % (info.get("name") or ""))
        ttk.Label(window, textvariable=tip, background=BG).pack(anchor="w", padx=10, pady=(4, 0))
        bar = ttk.Frame(window)
        bar.pack(fill="x", padx=10, pady=8)
        ttk.Button(bar, text="关闭", command=self._close_demo).pack(side="right")
        window.protocol("WM_DELETE_WINDOW", self._close_demo)
        self._demo_window = window
        self._demo_stop.clear()
        self._demo_play(view, info)

    def _demo_play(self, view, info) -> None:
        text = str(info.get("text") or "")
        index = [0]

        def step():
            if self._demo_window is None or self._demo_stop.is_set():
                return
            if index[0] >= len(text):
                self._demo_after = None
                return
            view.insert("end", text[index[0]])
            view.see("end")
            index[0] += 1
            try:
                self._demo_after = self.root.after(35, step)
            except Exception:
                self._demo_after = None

        step()

    def _close_demo(self) -> None:
        self._demo_stop.set()
        if self._demo_after is not None:
            try:
                self.root.after_cancel(self._demo_after)
            except Exception:
                pass
            self._demo_after = None
        window = self._demo_window
        self._demo_window = None
        if window is not None:
            try:
                window.destroy()
            except Exception:
                pass

    # ------------------------------------------------------------ 状态刷新
    def on_status(self, info: dict) -> None:
        try:
            state_label = info.get("state_label") or ""
            segment = int(info.get("segment") or 0)
            segments = int(info.get("segments") or 0)
            step_name = str(info.get("step_name") or "")
            percent = float(info.get("percent") or 0)
            text = state_label
            if segments:
                text += " · 第 %d/%d 层" % (segment, segments)
            if step_name:
                text += "：%s" % step_name
            if segments or percent:
                text += " · %.1f%%" % percent
            self.status_var.set(text)
            self.detail_var.set("已打 %d / %d 个字符"
                                % (info.get("position") or 0, info.get("total") or 0))
            message = str(info.get("message") or "")
            if message:
                self.hint_var.set(message)
        except Exception:
            pass
