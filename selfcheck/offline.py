# -*- coding: utf-8 -*-
"""离线自测：不真的按键，也能验证核心逻辑是否正确。

运行方式：
    python AutoTyper.pyw --selftest
    python selfcheck/offline.py --with-windows      # 额外验证键盘钩子/托盘/界面样式

重点是第 2、3 个用例：用一个“带退格语义的假编辑器”接住所有动作，
如果“打错→退格→改对”的连招有 bug，最终文本就会和原文不一致，测试立刻失败。
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROGRAM_DIR = os.path.dirname(HERE)
if PROGRAM_DIR not in sys.path:
    sys.path.insert(0, PROGRAM_DIR)

from autotyper import paths  # noqa: E402
from autotyper.config import Config  # noqa: E402
from autotyper.engine import (TypingEngine, apply_indent, build_segments,  # noqa: E402
                              build_steps_layout, resolve_indent_mode, segment_end_for)
from autotyper.habits import (MIN_INTERVALS, SAMPLE_TEXT, HabitError, HabitProfiler,  # noqa: E402
                              analyze_events, classify_key, profile_to_config, suggest, summarize)
from autotyper.humanizer import Humanizer  # noqa: E402

RESULTS: list = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(condition), detail))
    print("[%s] %s%s" % ("PASS" if condition else "FAIL", name, (" — " + detail) if detail else ""))
    return bool(condition)


# --------------------------------------------------------------------- 假输出端
class DocSink:
    """模拟一个文本编辑器：记录字符，退格会真的删除最后一个字符。"""

    name = "doc"

    def __init__(self) -> None:
        self.parts: list = []
        self.lock = threading.Lock()
        self.stats = {"typed": 0, "backspaces": 0, "keys": 0}

    def type_text(self, text: str) -> None:
        with self.lock:
            self.parts.append(text)
            self.stats["typed"] += len(text)

    def press_key(self, key: str) -> None:
        with self.lock:
            self.stats["keys"] += 1
            if key in ("enter", "return"):
                self.parts.append("\n")
            elif key == "tab":
                self.parts.append("\t")
            elif key in ("backspace", "back"):
                self._back()

    def backspace(self, count: int) -> None:
        with self.lock:
            for _ in range(count):
                self._back()

    def _back(self) -> None:
        self.stats["backspaces"] += 1
        if not self.parts:
            return
        last = self.parts.pop()
        if len(last) > 1:
            self.parts.append(last[:-1])

    def foreground_signature(self):
        return (0, 0)

    def process_name(self) -> str:
        return "selftest.exe"

    def text(self) -> str:
        with self.lock:
            return "".join(self.parts)


class AutoIndentPairSink:
    """模拟 PyCharm 一类编辑器：Enter 自动缩进、括号/引号自动配对。

    用来验证“多行文本 + 打错后回头改”不会把行/缩进/括号搞乱。
    """

    name = "auto-indent"
    PAIRS = {"(": ")", "[": "]", "{": "}", "'": "'", '"': '"'}

    def __init__(self, initial: str = "    ", indent: str = "    ") -> None:
        self.buffer = list(initial)
        self.cursor = len(self.buffer)
        self.indent = indent

    def type_text(self, text: str) -> None:
        for ch in text:
            if ch in self.PAIRS:
                # 编辑器自动补一个右半边，光标停在中间
                self.buffer.insert(self.cursor, ch)
                self.cursor += 1
                self.buffer.insert(self.cursor, self.PAIRS[ch])
            elif self.cursor < len(self.buffer) and self.buffer[self.cursor] == ch:
                # 打到自动补出来的右半边 → 直接跨过去
                self.cursor += 1
            else:
                self.buffer.insert(self.cursor, ch)
                self.cursor += 1

    def press_key(self, key: str) -> None:
        key = str(key).lower()
        if key in ("enter", "return"):
            self.buffer.insert(self.cursor, "\n")
            self.cursor += 1
            for ch in self.indent:
                self.buffer.insert(self.cursor, ch)
                self.cursor += 1
        elif key in ("backspace", "back"):
            if self.cursor > 0:
                self.cursor -= 1
                del self.buffer[self.cursor]

    def text(self) -> str:
        return "".join(self.buffer)


# --------------------------------------------------------------------- 工具
# 放在程序目录内，并且用 os.makedirs 而不是 tempfile.mkdtemp：
# mkdtemp 会创建“保护性 ACL”的目录，导致受限沙箱下无法往里写文件。
TMP = os.path.join(PROGRAM_DIR, ".selftest-tmp")
os.makedirs(TMP, exist_ok=True)
_original_state_path = paths.STATE_PATH
paths.STATE_PATH = os.path.join(TMP, "state.json")


_script_counter = [0]


def make_config(text: str, **overrides) -> Config:
    _script_counter[0] += 1
    script = os.path.join(TMP, "script_%d.txt" % _script_counter[0])
    with open(script, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    data = {
        "source": {"mode": "script", "script": script, "segment_mode": "all",
                   "indent_mode": "keep", "resume_last_position": False},
        "timing": {"start_delay_ms": 0, "char_delay_ms": 3, "char_delay_jitter_pct": 20,
                   "thinking_pause_chance": 0.0, "burst_mode": False, "newline_pause_ms": 0,
                   "punctuation_pause_ms": 0, "space_pause_ms": 0},
        "human": {"typo_rate": 0.0, "transpose_rate": 0.0, "double_char_rate": 0.0},
        "safety": {"pause_on_focus_change": False, "max_chars_per_trigger": 0},
        "behavior": {"newline_mode": "enter"},
    }
    for section, values in overrides.items():
        data.setdefault(section, {}).update(values)
    return Config(data, os.path.join(TMP, "config.json"))


def wait_done(engine: TypingEngine, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not engine.status()["typing"]:
            return True
        time.sleep(0.02)
    return False


# --------------------------------------------------------------------- 用例
def test_structs() -> None:
    from autotyper.win32_input import selftest_structs
    info = selftest_structs()
    check("SendInput 结构体尺寸正确", info["ok"], str(info))


def test_segments() -> None:
    text = "aaa\nbbb\n\nccc\n"
    line = build_segments(text, "line")
    check("分段：按行", line == [(0, 4), (4, 8), (8, 9), (9, 13)], str(line))
    block = build_segments(text, "block")
    check("分段：按段落", block[0] == (0, 9) and block[1] == (9, 13), str(block))
    chars = build_segments("abcdefg", "chars", 3)
    check("分段：固定字数", chars == [(0, 3), (3, 6), (6, 7)], str(chars))
    check("分段：整体", build_segments(text, "all") == [(0, len(text))])
    check("分段：定位下一段终点", segment_end_for(line, 5) == 8)


def test_indent() -> None:
    text = "def f():\n    x = 1\n    return x\n"
    check("缩进：原样保留", apply_indent(text, "keep") == text)
    check("缩进：全部去掉", apply_indent(text, "strip") == "def f():\nx = 1\nreturn x\n")
    check("缩进：只去公共缩进",
          apply_indent("    a\n        b\n", "relative") == "a\n    b\n")
    check("缩进：自动识别 IDE", resolve_indent_mode("auto", "pycharm64.exe") == "strip")
    check("缩进：普通程序保留", resolve_indent_mode("auto", "notepad.exe") == "keep")


def test_humanizer() -> None:
    import random
    timing = {"char_delay_ms": 10, "char_delay_jitter_pct": 30, "burst_mode": True,
              "burst_fast_ms": 5, "burst_slow_ms": 20, "thinking_pause_chance": 0.05,
              "burst_len_min": 3, "burst_len_max": 8}
    human = {"typo_rate": 0.3, "transpose_rate": 0.05, "double_char_rate": 0.05,
             "late_notice_rate": 0.5, "late_notice_extra_max": 2, "fix_delay_min_ms": 5,
             "fix_delay_max_ms": 10}
    text = "hello world, this is a test of the humanizer engine.\nprint('ok')\n"
    actions = list(Humanizer(timing, human, rng=random.Random(7)).actions(text))
    check("拟人化：动作流非空", len(actions) > 0, "%d 个动作" % len(actions))
    check("拟人化：确实模拟了打错", any(a.plan_id is not None for a in actions))
    delayed = [a.delay_ms for a in actions]
    check("拟人化：间隔有波动", len(set(delayed)) > 3, "不同间隔 %d 种" % len(set(delayed)))
    same = list(Humanizer(timing, human, rng=random.Random(7)).actions(text))
    check("拟人化：同种子可复现", [a.delay_ms for a in same] == delayed)
    indexes = [a.progress_index for a in actions]
    check("拟人化：进度下标单调不减", all(b >= a for a, b in zip(indexes, indexes[1:])))
    check("拟人化：进度不超过文本长度", max(indexes) <= len(text), str(max(indexes)))


def test_humanizer_multiline_editor() -> None:
    """多行文本 + 自动缩进/自动配对编辑器：错字修正不能把结构打歪。"""
    import random
    original = ("    pad_x = int((x2 - x1) * pad_ratio)\n"
                "    pad_y = int((y2 - y1) * pad_ratio)\n")
    stripped = apply_indent(original, "strip")
    timing = {"char_delay_ms": 1, "burst_mode": False, "newline_pause_ms": 0,
              "punctuation_pause_ms": 0, "space_pause_ms": 0,
              "thinking_pause_chance": 0, "char_delay_jitter_pct": 0}
    human = {"typo_rate": 1.0, "transpose_rate": 0.02, "double_char_rate": 0.02,
             "late_notice_rate": 1.0, "late_notice_extra_max": 3,
             "fix_delay_min_ms": 0, "fix_delay_max_ms": 0, "neighbor_typo_ratio": 1.0}
    unsafe = set("\r\n\t()[]{}'\"\x60")
    bad_actions = []
    rounds = 12
    for seed in range(rounds):
        actions = list(Humanizer(timing, human, rng=random.Random(seed)).actions(stripped, "enter"))
        for action in actions:
            if action.plan_id is None:
                continue
            if action.kind == "key" and action.key == "enter":
                bad_actions.append("enter")
            if action.kind == "text" and any(ch in unsafe for ch in action.text):
                bad_actions.append("unsafe-text")
        sink = AutoIndentPairSink()
        for action in actions:
            if action.kind == "text":
                sink.type_text(action.text)
            elif action.kind == "key":
                sink.press_key(action.key)
        got_lines = [line.rstrip(" \t") for line in sink.text().split("\n")]
        want_lines = [line.rstrip(" \t") for line in original.split("\n")]
        if got_lines != want_lines:
            bad_actions.append("第 %d 轮内容不一致：%r" % (seed + 1, sink.text()[:80]))
            break
    check("拟人化：多行错字修正不会按 Enter/括号/引号", not bad_actions,
          bad_actions[0] if bad_actions else "")
    check("拟人化：自动缩进+自动配对编辑器里多行内容仍完全正确",
          not any(item.startswith("第 ") for item in bad_actions),
          bad_actions[0] if bad_actions else "%d 轮全部一致" % rounds)


def test_habits_calibration() -> None:
    """用户打字习惯校准：统计口径、suggest 映射/clamp、样本不足报错都要稳。"""
    # ---------------------------------------------------------- 按键分类
    check("习惯：BackSpace 归类为退格", classify_key("", "BackSpace") == "backspace")
    check("习惯：Return / KP_Enter 归类为回车",
          classify_key("\r", "Return") == "enter" and classify_key("", "KP_Enter") == "enter")
    check("习惯：空格单独分类", classify_key(" ") == "space")
    check("习惯：英文与中文标点都算标点",
          classify_key(",") == "punct" and classify_key("。") == "punct"
          and classify_key("！") == "punct")
    check("习惯：普通字符（含中文）算字符",
          classify_key("a") == "char" and classify_key("中") == "char")
    check("习惯：换行/制表/功能键/多字符不参与统计",
          classify_key("\n") == "" and classify_key("\t") == ""
          and classify_key("", "Shift_L") == "" and classify_key("ab") == "")

    # ---------------------------------------------------------- 采集样例
    check("习惯：采集样例长度合适、符号齐全",
          100 <= len(SAMPLE_TEXT) <= 130 and " " in SAMPLE_TEXT and "," in SAMPLE_TEXT
          and "." in SAMPLE_TEXT and "\n" in SAMPLE_TEXT and "(" in SAMPLE_TEXT
          and "\"" in SAMPLE_TEXT,
          "长度 %d" % len(SAMPLE_TEXT))

    # ---------------------------------------------------------- 纯节奏样本
    # 20 个间隔严格按 60/120 交替：中位 90、p10=60、p90=120、抖动 67%、字速 667
    rhythm_events = [{"t": 1000.0, "char": "a"}]
    moment = 1000.0
    for index in range(20):
        moment += 60.0 if index % 2 == 0 else 120.0
        rhythm_events.append({"t": moment, "char": "a"})
    profiler = HabitProfiler()
    for item in rhythm_events:
        profiler.record(item["t"], char=item["char"])
    check("习惯：字符数 / 有效间隔数统计正确",
          profiler.char_count == 21 and profiler.interval_count == 20,
          "%d 字 / %d 间隔" % (profiler.char_count, profiler.interval_count))
    profile = analyze_events(rhythm_events)
    check("习惯：profile 键与契约一致（14 项，note 为字符串）",
          set(profile) == {"sample_chars", "sample_seconds", "median_interval_ms",
                           "p10_interval_ms", "p90_interval_ms", "jitter_pct",
                           "chars_per_minute", "backspace_count", "typo_rate",
                           "space_pause_ms", "punctuation_pause_ms", "newline_pause_ms",
                           "fix_delay_ms", "note"} and isinstance(profile["note"], str))
    check("习惯：中位 / 分位间隔统计正确",
          profile["median_interval_ms"] == 90 and profile["p10_interval_ms"] == 60
          and profile["p90_interval_ms"] == 120,
          "%s / %s / %s" % (profile["median_interval_ms"], profile["p10_interval_ms"],
                            profile["p90_interval_ms"]))
    check("习惯：抖动 / 字速 / 时长 / 字数统计正确",
          profile["jitter_pct"] == 67 and profile["chars_per_minute"] == 667
          and profile["sample_seconds"] == 1.8 and profile["sample_chars"] == 21,
          "%s%% / %s 字每分 / %ss" % (profile["jitter_pct"], profile["chars_per_minute"],
                                      profile["sample_seconds"]))
    check("习惯：没有空格/标点/换行/退格样本时对应项为 None",
          profile["space_pause_ms"] is None and profile["punctuation_pause_ms"] is None
          and profile["newline_pause_ms"] is None and profile["fix_delay_ms"] is None)
    check("习惯：summarize 含中位间隔/字速/抖动/样本/退格",
          summarize(profile) == "中位间隔 90 ms · 约 667 字/分 · 抖动 67% · 样本 21 字 · 退格 0 次",
          summarize(profile))
    check("习惯：HabitProfiler.analyze() 无参调用可用于界面",
          profiler.analyze()["median_interval_ms"] == 90
          and profiler.analyze({"char_delay_ms": 55}, {"typo_rate": 0.02})["median_interval_ms"] == 90)

    plain_suggest = suggest(profile, {}, {})
    check("习惯：suggest 基础节奏映射正确",
          plain_suggest == {"timing.char_delay_ms": 90,
                            "timing.char_delay_jitter_pct": 67,
                            "timing.burst_mode": True,
                            "timing.burst_fast_ms": 60,
                            "timing.burst_slow_ms": 120},
          str(plain_suggest))
    check("习惯：样本不足时不返回暂停 / 错字 / 改错参数",
          "timing.space_pause_ms" not in plain_suggest
          and "timing.punctuation_pause_ms" not in plain_suggest
          and "timing.newline_pause_ms" not in plain_suggest
          and "human.typo_rate" not in plain_suggest
          and "human.fix_delay_min_ms" not in plain_suggest)
    plain_config = profile_to_config(profile)
    check("习惯：profile_to_config 只输出 JSON 安全数值字段",
          plain_config == {"sample_chars": 21, "sample_seconds": 1.8,
                           "median_interval_ms": 90, "jitter_pct": 67,
                           "chars_per_minute": 667, "typo_rate": 0.0}
          and json.loads(json.dumps(plain_config)) == plain_config,
          str(plain_config))

    # ---------------------------------------------------------- 混合样本
    # 40 字（间隔 60/120 交替）+ 4 空格（各 +170ms）+ 4 标点（各 +270ms）
    # + 2 回车（各 +470ms）+ 2 次退格（间隔 150ms）
    mixed_events = [{"t": 1000.0, "kind": "char"}]
    moment = 1000.0
    for index in range(39):
        moment += 60.0 if index % 2 == 0 else 120.0
        mixed_events.append({"t": moment, "kind": "char"})
    for _ in range(4):
        moment += 290.0
        mixed_events.append({"t": moment, "kind": "space"})
    for _ in range(4):
        moment += 390.0
        mixed_events.append({"t": moment, "kind": "punct"})
    for _ in range(2):
        moment += 590.0
        mixed_events.append({"t": moment, "kind": "enter"})
    moment += 200.0
    mixed_events.append({"t": moment, "kind": "backspace"})
    moment += 150.0
    mixed_events.append({"t": moment, "kind": "backspace"})

    mixed = analyze_events(mixed_events)
    check("习惯：混合样本的字数 / 退格数正确",
          mixed["sample_chars"] == 48 and mixed["backspace_count"] == 2,
          "%d 字 / %d 退格" % (mixed["sample_chars"], mixed["backspace_count"]))
    check("习惯：混合样本的中位/分位/抖动/字速正确",
          mixed["median_interval_ms"] == 120 and mixed["p10_interval_ms"] == 60
          and mixed["p90_interval_ms"] == 290 and mixed["jitter_pct"] == 192
          and mixed["chars_per_minute"] == 500,
          "%s / %s / %s / %s%% / %s 字每分"
          % (mixed["median_interval_ms"], mixed["p10_interval_ms"],
             mixed["p90_interval_ms"], mixed["jitter_pct"], mixed["chars_per_minute"]))
    check("习惯：退格率 = 退格数 / 字数", mixed["typo_rate"] == 0.0417,
          str(mixed["typo_rate"]))
    check("习惯：空格/标点/换行/改错停顿统计正确",
          mixed["space_pause_ms"] == 170 and mixed["punctuation_pause_ms"] == 270
          and mixed["newline_pause_ms"] == 470 and mixed["fix_delay_ms"] == 150,
          "%s / %s / %s / %s" % (mixed["space_pause_ms"], mixed["punctuation_pause_ms"],
                                 mixed["newline_pause_ms"], mixed["fix_delay_ms"]))
    check("习惯：采样时长 = 首尾按键时间差", mixed["sample_seconds"] == 7.73,
          str(mixed["sample_seconds"]))

    mixed_suggest = suggest(mixed, {}, {})
    check("习惯：suggest 全套映射（抖动 clamp 到 80%）",
          mixed_suggest == {"timing.char_delay_ms": 120,
                            "timing.char_delay_jitter_pct": 80,
                            "timing.burst_mode": True,
                            "timing.burst_fast_ms": 60,
                            "timing.burst_slow_ms": 290,
                            "timing.space_pause_ms": 170,
                            "timing.punctuation_pause_ms": 270,
                            "timing.newline_pause_ms": 470,
                            "human.typo_rate": 0.0417,
                            "human.fix_delay_min_ms": 90,
                            "human.fix_delay_max_ms": 270},
          str(mixed_suggest))
    check("习惯：suggest 不返回换位/连击/晚发现/快慢长度等其它参数",
          not [key for key in mixed_suggest
               if any(word in key for word in ("transpose", "double", "late_notice",
                                               "burst_len", "neighbor", "skip_fix"))])

    # ---------------------------------------------------------- 极端值与缺省
    extreme_low = suggest({"median_interval_ms": 1, "jitter_pct": 1, "p10_interval_ms": 1,
                           "p90_interval_ms": 5000, "sample_chars": 100,
                           "backspace_count": 0}, {}, {})
    check("习惯：极端参数（1ms / 0 退格）clamp 正确",
          extreme_low == {"timing.char_delay_ms": 20,
                          "timing.char_delay_jitter_pct": 5,
                          "timing.burst_mode": True,
                          "timing.burst_fast_ms": 15,
                          "timing.burst_slow_ms": 500,
                          "human.typo_rate": 0.0},
          str(extreme_low))
    extreme_high = suggest({"median_interval_ms": 5000, "jitter_pct": 500,
                            "p10_interval_ms": 5000, "p90_interval_ms": 9000,
                            "sample_chars": 10, "backspace_count": 5, "fix_delay_ms": 0,
                            "space_pause_ms": -80, "punctuation_pause_ms": 9999,
                            "newline_pause_ms": 9999}, {}, {})
    check("习惯：极端参数（5000ms / 样本不足）clamp 且缺省不返回",
          extreme_high == {"timing.char_delay_ms": 220,
                           "timing.char_delay_jitter_pct": 80,
                           "timing.burst_mode": True,
                           "timing.burst_fast_ms": 220,
                           "timing.burst_slow_ms": 500,
                           "timing.space_pause_ms": 0,
                           "timing.punctuation_pause_ms": 600,
                           "timing.newline_pause_ms": 1500,
                           "human.fix_delay_min_ms": 60,
                           "human.fix_delay_max_ms": 140},
          str(extreme_high))

    # ---------------------------------------------------------- 过滤 / 样本不足 / 重置
    tricky = [0.0, 100.0, 105.0, 200.0, 2105.0, 2405.0, 2413.0]
    filtered = HabitProfiler()
    for stamp in tricky:
        filtered.record(stamp, char="a")
    check("习惯：只保留 10~3000ms 的间隔",
          filtered.char_count == 7 and filtered.interval_count == 4
          and filtered.intervals == [100.0, 95.0, 1905.0, 300.0],
          str(filtered.intervals))
    filtered.reset()
    check("习惯：reset 清空采集", filtered.char_count == 0 and filtered.interval_count == 0
          and filtered.events == [])

    caught = ""
    try:
        analyze_events([{"t": index * 100.0, "kind": "char"} for index in range(20)])
    except HabitError:
        caught = "HabitError"
    except Exception as exc:
        caught = type(exc).__name__
    check("习惯：有效间隔不足 %d 个时抛 HabitError" % MIN_INTERVALS,
          caught == "HabitError", caught)
    check("习惯：HabitError 是 ValueError 的子类", issubclass(HabitError, ValueError))
    enough = analyze_events([{"t": index * 100.0, "kind": "char"} for index in range(21)])
    check("习惯：刚好 %d 个间隔即可分析" % MIN_INTERVALS,
          enough["median_interval_ms"] == 100 and enough["chars_per_minute"] == 600,
          "%s ms / %s 字每分" % (enough["median_interval_ms"], enough["chars_per_minute"]))
    with_context = analyze_events(mixed_events, timing={"char_delay_ms": 55},
                                  human={"typo_rate": 0.02})
    check("习惯：analyze 接受 timing/human 上下文且结果不变", with_context == mixed)


def test_engine_full_run() -> None:
    text = "def add(a, b):\n    return a + b\n\n\nprint(add(1, 2))\n"
    cfg = make_config(text, human={"typo_rate": 0.35, "transpose_rate": 0.05,
                                   "double_char_rate": 0.05, "late_notice_rate": 0.4,
                                   "fix_delay_min_ms": 2, "fix_delay_max_ms": 5})
    sink = DocSink()
    engine = TypingEngine(cfg, sink=sink)
    ok, message = engine.trigger()
    check("引擎：可以开始", ok, message)
    check("引擎：能跑完", wait_done(engine))
    check("引擎：文本完全一致（含错字修正）", sink.text() == text,
          "得到 %r" % sink.text()[:60])
    check("引擎：真的退格过", sink.stats["backspaces"] > 0, str(sink.stats))
    check("引擎：状态为打完", engine.status()["state"] == "done", engine.status()["state"])


def test_engine_stop_and_resume() -> None:
    text = ("import random\n\n\ndef quicksort(items):\n"
            "    if len(items) <= 1:\n        return items\n"
            "    pivot = items[len(items) // 2]\n"
            "    left = [x for x in items if x < pivot]\n"
            "    right = [x for x in items if x > pivot]\n"
            "    return quicksort(left) + quicksort(right)\n")
    cfg = make_config(text, timing={"char_delay_ms": 4},
                      human={"typo_rate": 0.2, "late_notice_rate": 0.5,
                             "fix_delay_min_ms": 2, "fix_delay_max_ms": 4})
    sink = DocSink()
    engine = TypingEngine(cfg, sink=sink)
    engine.trigger()
    time.sleep(0.25)
    engine.stop()
    check("引擎：停止后线程结束", wait_done(engine, 10))
    first = sink.text()
    check("引擎：停止时确实只打了一部分", 0 < len(first) < len(text), "%d 字符" % len(first))
    engine.trigger()
    check("引擎：可以继续", wait_done(engine, 60))
    check("引擎：续打后全文一致（无重复无缺失）", sink.text() == text,
          "长度 %d / %d" % (len(sink.text()), len(text)))


def test_engine_line_mode() -> None:
    text = "one\ntwo\nthree\n"
    cfg = make_config(text, source={"mode": "script", "segment_mode": "line"})
    sink = DocSink()
    engine = TypingEngine(cfg, sink=sink)
    check("分段打字：第一段只打第一行", engine.trigger()[0] and wait_done(engine) and sink.text() == "one\n",
          repr(sink.text()))
    engine.trigger()
    wait_done(engine)
    check("分段打字：第二段接着打", sink.text() == "one\ntwo\n", repr(sink.text()))
    engine.trigger()
    wait_done(engine)
    check("分段打字：全部打完", sink.text() == text, repr(sink.text()))
    ok, message = engine.trigger()
    check("分段打字：打完后再按 ↓ 会提示", (not ok) and ("打完" in message), message)


def test_config_roundtrip() -> None:
    target = os.path.join(TMP, "roundtrip.json")
    cfg = Config({"timing": {"char_delay_ms": 77}}, target)
    cfg.save()
    from autotyper.config import load as load_config
    reloaded = load_config(target)
    check("配置：保存后能读回", reloaded.get("timing.char_delay_ms") == 77,
          str(reloaded.get("timing.char_delay_ms")))
    check("配置：缺失项用默认值补齐", reloaded.get("human.typo_rate") == 0.025)
    with open(target, "w", encoding="utf-8") as handle:
        handle.write("{ 这不是合法 JSON ")
    broken = load_config(target)
    check("配置：坏文件也能启动", broken.get("timing.char_delay_ms") == 55 and broken.load_warnings)


def test_hotkey_matching() -> None:
    from autotyper.hotkeys import KBDLLHOOKSTRUCT, WM_KEYDOWN, WM_KEYUP, GlobalHotkeys
    from autotyper.win32_input import name_to_vk
    captured = []
    hotkeys = GlobalHotkeys({"start": "down", "stop": "up", "panic": "f12", "reset": "f9"},
                            swallow=True, on_action=captured.append)
    check("热键：键名映射正确", name_to_vk("down") == 0x28 and name_to_vk("f12") == 0x7B,
          "%s %s" % (name_to_vk("down"), name_to_vk("f12")))

    def send(vk: int, message: int, injected: bool = False) -> int:
        info = KBDLLHOOKSTRUCT()
        info.vkCode = vk
        info.flags = 0x10 if injected else 0
        return int(hotkeys._callback(0, message, ctypes.addressof(info)))

    swallowed_down = send(0x28, WM_KEYDOWN)
    check("热键：↓ 被识别并吞掉", swallowed_down == 1, "返回值 %s" % swallowed_down)
    send(0x28, WM_KEYDOWN)      # 长按自动重复（没有 KEYUP）→ 不应重复触发
    send(0x28, WM_KEYUP)
    send(0x26, WM_KEYDOWN)
    send(0x26, WM_KEYUP)
    sent = []
    while not hotkeys._queue.empty():
        sent.append(hotkeys._queue.get_nowait())
    check("热键：开始/停止各触发一次", sent == ["start", "stop"], str(sent))
    passthrough = send(0x41, WM_KEYDOWN)   # 普通字符 A 不应被吞
    check("热键：不相关按键放行", passthrough != 1, "返回值 %s" % passthrough)
    queued_injected = []
    send(0x28, WM_KEYDOWN, injected=True)
    while not hotkeys._queue.empty():
        queued_injected.append(hotkeys._queue.get_nowait())
    check("热键：忽略自己注入的按键", queued_injected == [], str(queued_injected))

    # 重置进度键（默认不绑定，用户自己设置后应能触发 reset）
    send(0x78, WM_KEYDOWN)      # F9
    send(0x78, WM_KEYUP)
    sent_reset = []
    while not hotkeys._queue.empty():
        sent_reset.append(hotkeys._queue.get_nowait())
    check("热键：重置进度键 F9 触发 reset", sent_reset == ["reset"], str(sent_reset))
    check("热键：启动日志描述里带重置进度", "重置进度" in hotkeys.describe(), hotkeys.describe())


def test_hook_install() -> None:
    from autotyper.hotkeys import GlobalHotkeys
    hotkeys = GlobalHotkeys({"start": "down"}, swallow=False, on_action=lambda name: None)
    ok, message = hotkeys.start()
    check("键盘钩子：能安装并卸载", ok, message)
    hotkeys.stop()
    check("键盘钩子：卸载后线程退出", not hotkeys.running)


def test_plain_text() -> None:
    """退格到空、超长文本、特殊字符等边界情况。"""
    text = "a\n\n\n中文字符测试：！（），。\n"
    cfg = make_config(text, human={"typo_rate": 0.4, "late_notice_extra_max": 3,
                                   "fix_delay_min_ms": 1, "fix_delay_max_ms": 3})
    sink = DocSink()
    engine = TypingEngine(cfg, sink=sink)
    engine.trigger()
    wait_done(engine)
    check("边界：中文与换行也能完全一致", sink.text() == text, repr(sink.text()))


def test_engine_random_stress(rounds: int = 40) -> None:
    """随机压测：不管怎么随机打错，最终文本必须和原文完全一致。

    这条用例专门盯着一个真实出现过的 bug——连击（同一个字打两下）连招
    少退一格，导致每发生一次就在文中永久多出一个字符。
    """
    samples = [
        "print('hello world')\n",
        "def add(a, b):\n    return a + b\n\n\nprint(add(1, 2))\n",
        "for i in range(10):\n    total += i * 2\n",
        "class Point:\n    def __init__(self, x, y):\n        self.x = x\n        self.y = y\n",
        "（中文注释）\nvalues = [1, 2, 3, 4, 5]\nprint(sum(values))\n",
        "if __name__ == '__main__':\n    main()\n",
    ]
    mismatches = []
    for round_index in range(rounds):
        text = samples[round_index % len(samples)]
        cfg = make_config(text, human={
            "typo_rate": 0.5, "transpose_rate": 0.15, "double_char_rate": 0.15,
            "late_notice_rate": 0.6, "late_notice_extra_max": 3,
            "fix_delay_min_ms": 0, "fix_delay_max_ms": 2})
        sink = DocSink()
        engine = TypingEngine(cfg, sink=sink)
        engine.trigger()
        if not wait_done(engine, 60):
            mismatches.append("第 %d 轮超时" % (round_index + 1))
            break
        if sink.text() != text:
            mismatches.append("第 %d 轮不一致：%r" % (round_index + 1, sink.text()[:80]))
    check("压测：%d 轮高强度随机错字后文本仍完全一致" % rounds, not mismatches,
          mismatches[0] if mismatches else "")


def make_steps_config(steps, **overrides) -> Config:
    data = {
        "source": {"mode": "steps", "indent_mode": "keep", "resume_last_position": False},
        "steps": steps,
        "timing": {"start_delay_ms": 0, "char_delay_ms": 2, "char_delay_jitter_pct": 0,
                   "thinking_pause_chance": 0.0, "burst_mode": False, "newline_pause_ms": 0,
                   "punctuation_pause_ms": 0, "space_pause_ms": 0},
        "human": {"typo_rate": 0.0, "transpose_rate": 0.0, "double_char_rate": 0.0},
        "safety": {"pause_on_focus_change": False, "max_chars_per_trigger": 0},
        "behavior": {"newline_mode": "enter"},
    }
    for section, values in overrides.items():
        data.setdefault(section, {}).update(values)
    return Config(data, os.path.join(TMP, "steps-config.json"))


def test_steps_layout() -> None:
    text, segments, names = build_steps_layout([
        {"name": "甲", "text": "x=1"},
        {"name": "乙", "text": "y=2\nz=3"},
    ])
    check("楼层：拼接与分段正确",
          text == "x=1\ny=2\nz=3" and segments == [(0, 4), (4, 11)] and names == ["甲", "乙"],
          "%r %s %s" % (text, segments, names))
    text2, segments2, names2 = build_steps_layout([
        {"name": "空层", "text": "   "},
        {"name": "有内容", "text": "ok"},
    ])
    check("楼层：空楼层被跳过", text2 == "ok" and segments2 == [(0, 2)] and names2 == ["有内容"],
          "%r %s %s" % (text2, segments2, names2))


def test_engine_steps_mode() -> None:
    steps = [{"name": "第一层", "text": "print(1)"},
             {"name": "第二层", "text": "print(2)"},
             {"name": "第三层", "text": "print(3)"}]
    cfg = make_steps_config(steps)
    sink = DocSink()
    engine = TypingEngine(cfg, sink=sink)
    engine.trigger()
    wait_done(engine)
    check("楼层：按一下 ↓ 只打第一层", sink.text() == "print(1)\n", repr(sink.text()))
    engine.trigger()
    wait_done(engine)
    check("楼层：再按 ↓ 打第二层", sink.text() == "print(1)\nprint(2)\n", repr(sink.text()))
    status = engine.status()
    check("楼层：状态里有层号与层名",
          status.get("segment") == 3 and status.get("step_name") == "第三层" and status.get("steps_mode"),
          "%s / %s" % (status.get("segment"), status.get("step_name")))
    engine.trigger()
    wait_done(engine)
    check("楼层：三层打完内容完全正确", sink.text() == "print(1)\nprint(2)\nprint(3)", repr(sink.text()))


def test_auto_enter_option() -> None:
    """每层打完是否自动回车，必须可配。"""
    steps = [{"name": "一", "text": "aa"}, {"name": "二", "text": "bb"}]
    text_on, seg_on, _n = build_steps_layout(steps)
    check("自动回车：默认开 → 层之间自动换行", text_on == "aa\nbb" and seg_on == [(0, 3), (3, 5)],
          "%r %s" % (text_on, seg_on))
    text_off, seg_off, _n2 = build_steps_layout(steps, auto_enter=False)
    check("自动回车：关掉 → 层之间不加换行", text_off == "aabb" and seg_off == [(0, 2), (2, 4)],
          "%r %s" % (text_off, seg_off))
    cfg = make_steps_config(steps, source={"auto_enter": False})
    sink = DocSink()
    engine = TypingEngine(cfg, sink=sink)
    engine.trigger()
    wait_done(engine)
    engine.trigger()
    wait_done(engine)
    check("自动回车关闭：两层连着打，不会多出回车", sink.text() == "aabb", repr(sink.text()))


def test_swallow_and_lock() -> None:
    """拦截范围（只在目标程序里吞键）与演示模式（锁字母键）的安全性。"""
    import ctypes as _ctypes
    from autotyper.hotkeys import GlobalHotkeys, KBDLLHOOKSTRUCT, WM_KEYDOWN, WM_KEYUP
    captured = []
    state = {"swallow": False}
    hotkeys = GlobalHotkeys({"start": "down", "stop": "up"}, swallow=True,
                            swallow_gate=lambda: state["swallow"], on_action=captured.append)

    def send(vk, msg):
        info = KBDLLHOOKSTRUCT()
        info.vkCode = vk
        info.flags = 0
        return int(hotkeys._callback(0, msg, _ctypes.addressof(info)))

    r = send(0x28, WM_KEYDOWN)
    send(0x28, WM_KEYUP)
    check("拦截范围：不在目标程序里时 ↓ 放行（别的软件能正常用）", r != 1, "返回值 %s" % r)
    state["swallow"] = True
    r2 = send(0x28, WM_KEYDOWN)
    send(0x28, WM_KEYUP)
    check("拦截范围：在目标程序里 ↓ 被吞掉（不影响打字光标）", r2 == 1, "返回值 %s" % r2)

    hotkeys.set_key_lock(True)
    check("演示模式：开启后状态正确", hotkeys.key_locked)
    check("演示模式：字母键 A 被拦住（打不进任何程序）", send(0x41, WM_KEYDOWN) == 1)
    check("演示模式：空格等非字母键照常可用", send(0x20, WM_KEYDOWN) != 1)
    check("演示模式：↓ 热键依然可用", send(0x28, WM_KEYDOWN) == 1)
    hotkeys.set_key_lock(False)
    check("演示模式：关闭后字母键立刻恢复", send(0x41, WM_KEYDOWN) != 1)
    hotkeys.set_key_lock(True)
    hotkeys.stop()
    check("演示模式：程序停止时一定自动解锁", not hotkeys.key_locked)


def test_modifier_release() -> None:
    """修饰键残留：开打前要把还按着的 Shift/Ctrl/Alt/Win 松开。"""
    from autotyper.win32_input import MODIFIER_KEYS, pending_modifiers, release_modifiers

    state = {"shift": True, "ctrl": False, "alt": True, "win": False}
    vk_map = {0xA0: "shift", 0xA1: "shift", 0xA2: "ctrl", 0xA3: "ctrl",
              0xA4: "alt", 0xA5: "alt", 0x5B: "win", 0x5C: "win"}

    def fake_down(vk):
        return bool(state.get(vk_map.get(vk, ""), False))

    names = pending_modifiers(key_down=fake_down)
    check("修饰键：能识别出还按着的 Shift 与 Alt", names == ["Shift", "Alt"], str(names))

    captured = []
    released = release_modifiers(key_down=fake_down, sender=lambda items: captured.extend(items))
    check("修饰键：只释放按着的那些（Shift/Alt）", released == ["Shift", "Alt"], str(released))
    check("修饰键：每个按着的键都发一个 KEYUP 事件", len(captured) == 4, "%d 个事件" % len(captured))
    from autotyper.win32_input import KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP
    flags = [int(item.ki.dwFlags) for item in captured]
    vks = [int(item.ki.wVk) for item in captured]
    check("修饰键：事件都是“抬起”", all(flag & KEYEVENTF_KEYUP for flag in flags), str(flags))
    check("修饰键：没按的 Ctrl/Win 不发事件", not any(vk in (0xA2, 0xA3, 0x5B, 0x5C) for vk in vks), str(vks))
    check("修饰键：右 Alt 带扩展键标记", any(flag & KEYEVENTF_EXTENDEDKEY for flag in flags), str(flags))

    state.clear()
    check("修饰键：没按任何修饰键时什么都不做",
          release_modifiers(key_down=fake_down, sender=lambda items: captured.extend(items)) == []
          and len(captured) == 4)

    class ReleaseSink(DocSink):
        def __init__(self):
            super().__init__()
            self.released = 0
            self.order = []

        def release_modifiers(self):
            self.released += 1
            self.order.append("release")
            return ["Shift"]

        def type_text(self, text):
            self.order.append("type")
            super().type_text(text)

    sink = ReleaseSink()
    engine = TypingEngine(make_config("abc\n"), sink=sink)
    engine.trigger()
    wait_done(engine)
    check("修饰键：引擎开打前会先释放", sink.released == 1, "released=%d" % sink.released)
    check("修饰键：释放发生在打第一个字之前", sink.order[:2] == ["release", "type"], str(sink.order[:3]))
    check("修饰键：释放后内容依然正确", sink.text() == "abc\n", repr(sink.text()))

    sink2 = ReleaseSink()
    engine2 = TypingEngine(make_config("abc\n", safety={"release_modifiers": False}), sink=sink2)
    engine2.trigger()
    wait_done(engine2)
    check("修饰键：关掉开关就不释放", sink2.released == 0, "released=%d" % sink2.released)


def test_case_handling() -> None:
    """大小写：大写字母必须自动按 Shift，且要正确考虑 CapsLock。"""
    if sys.platform != "win32":
        return
    from autotyper.win32_input import VK_CAPITAL, _char_to_vk, user32
    caps = bool(int(user32.GetKeyState(VK_CAPITAL)) & 0x0001)
    upper = _char_to_vk("A")
    lower = _char_to_vk("a")
    check("大小写：字母 A 映射到虚拟键 A", bool(upper) and upper[0] == 0x41, str(upper))
    check("大小写：大写 A 的 Shift 判断与 CapsLock 相反",
          bool(upper) and upper[1] != caps, "caps=%s upper=%s" % (caps, upper))
    check("大小写：小写 a 的 Shift 判断与 CapsLock 相同",
          bool(lower) and lower[1] == caps, "caps=%s lower=%s" % (caps, lower))
    check("大小写：大写需要 Shift（CapsLock 关闭时）",
          bool(upper) and (upper[1] is True) == (not caps))
    check("大小写：感叹号这类符号会自动带 Shift",
          bool(_char_to_vk("!")) and _char_to_vk("!")[1] is True, str(_char_to_vk("!")))
    check("大小写：中文不做按键映射（走 Unicode 注入）", _char_to_vk("中") is None)
    from autotyper.win32_input import SendInputSink
    for mode in ("auto", "unicode"):
        sink = SendInputSink(input_mode=mode)
        check("输入方式：%s 模式可创建且接口齐全" % mode,
              hasattr(sink, "type_text") and hasattr(sink, "press_key")
              and sink.input_mode == mode)


def test_next_segment_text() -> None:
    """“预览打字效果”依赖的接口：能拿到即将打出的那一层。"""
    steps = [{"name": "第一层", "text": "aa"}, {"name": "第二层", "text": "bb"}]
    cfg = make_steps_config(steps)
    sink = DocSink()
    engine = TypingEngine(cfg, sink=sink)
    info = engine.next_segment_text()
    check("预览：还没打时返回第一层",
          info and info["text"] == "aa\n" and info["index"] == 1 and info["total"] == 2
          and info["name"] == "第一层", str(info))
    engine.trigger()
    wait_done(engine)
    info2 = engine.next_segment_text()
    check("预览：第一层打完后返回第二层",
          info2 and info2["text"] == "bb" and info2["index"] == 2, str(info2))
    engine.trigger()
    wait_done(engine)
    check("预览：全部打完后返回 None", engine.next_segment_text() is None)


def test_focus_guard() -> None:
    """焦点还在本程序自己的窗口上时，必须拒绝打字（否则会把内容打进设置界面）。"""
    import autotyper.engine as engine_module
    cfg = make_config("hello\n")
    sink = DocSink()
    engine = TypingEngine(cfg, sink=sink)
    original = engine_module.foreground_info
    engine_module.foreground_info = lambda: {
        "hwnd": 1, "pid": os.getpid(), "process": "python.exe", "title": "打字助手 · 设置"}
    try:
        ok, message = engine.trigger()
    finally:
        engine_module.foreground_info = original
    check("防护：焦点在自己窗口上时拒绝开始", (not ok) and ("焦点" in message), message)
    check("防护：拒绝时一个字都没打", sink.text() == "", repr(sink.text()))


def run_windows_only_tests() -> None:
    from autotyper.win32_input import IS_WINDOWS
    if not IS_WINDOWS:
        return
    try:
        import tkinter as tk
        from autotyper.win32_input import hide_from_taskbar, get_ex_style, toplevel_hwnd
        root = tk.Tk()
        root.withdraw()
        hwnd = toplevel_hwnd(root.winfo_id())
        applied = hide_from_taskbar(hwnd)
        style = get_ex_style(hwnd)
        check("界面：窗口被标记为工具窗口（不进任务栏）",
              applied and bool(style & 0x00000080) and not (style & 0x00040000), "exstyle=0x%X" % style)
        root.destroy()
    except Exception as exc:
        check("界面：窗口样式测试", False, str(exc))

    try:
        from autotyper.tray import TrayIcon, MenuItem
        from autotyper.win32_input import integrity_level
        level = integrity_level()
        tray = TrayIcon(tooltip="selftest", menu_provider=lambda: [MenuItem("x", "x")],
                        on_command=lambda ident: None)
        started = tray.start(timeout=5)
        time.sleep(0.4)
        tray.update_tooltip("selftest done")
        tray.stop()
        if started:
            check("托盘：图标能添加并移除", True, "Shell_NotifyIcon 成功")
        elif level == "Low":
            # 低完整性级别的进程不允许往资源管理器的托盘加图标（系统会拒绝访问），
            # 正常双击启动的程序是 Medium，不受影响，所以这里如实记为“跳过”。
            print("[SKIP] 托盘：图标能添加并移除 — 当前进程完整性级别为 Low，"
                  "系统禁止跨级别添加托盘图标；请在普通桌面环境下复测")
            RESULTS.append(("托盘：图标能添加并移除", True, "SKIP：Low 完整性级别"))
        else:
            check("托盘：图标能添加并移除", False, "完整性级别 %s 下仍失败" % level)
    except Exception as exc:
        check("托盘：图标能添加并移除", False, str(exc))


def run_all(with_windows: bool = False) -> int:
    print("=" * 68)
    print("打字助手 离线自测")
    print("=" * 68)
    test_structs()
    test_segments()
    test_indent()
    test_humanizer()
    test_humanizer_multiline_editor()
    test_habits_calibration()
    test_engine_full_run()
    test_engine_stop_and_resume()
    test_engine_line_mode()
    test_config_roundtrip()
    test_hotkey_matching()
    test_plain_text()
    test_engine_random_stress()
    test_steps_layout()
    test_engine_steps_mode()
    test_focus_guard()
    test_next_segment_text()
    test_auto_enter_option()
    test_swallow_and_lock()
    test_case_handling()
    test_modifier_release()
    if with_windows:
        test_hook_install()
        run_windows_only_tests()
    failed = [name for name, ok, _detail in RESULTS if not ok]
    print("-" * 68)
    print("共 %d 项，通过 %d 项，失败 %d 项" % (len(RESULTS), len(RESULTS) - len(failed), len(failed)))
    if failed:
        print("失败项：" + "、".join(failed))
    try:
        shutil.rmtree(TMP, ignore_errors=True)
    except Exception:
        pass
    paths.STATE_PATH = _original_state_path
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run_all(with_windows="--with-windows" in sys.argv))
