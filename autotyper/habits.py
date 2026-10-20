# -*- coding: utf-8 -*-
"""打字习惯校准：把“实时采集的击键时间戳”变成一套拟人参数。

纯逻辑，不 import tkinter / win32，可离线测试。界面只负责采集按键
（时间戳 + char + keysym），本模块负责统计与映射：
  1. 统计中位间隔 / 分位间隔 / 抖动 / 字速 / 退格率；
  2. 估算空格、标点、换行相对普通字符的额外停顿；
  3. 把统计结果映射成 timing / human 里的配置项（suggest）。
"""

from __future__ import annotations

import math
import statistics
from typing import Any, Dict, List, Optional

# 至少要有这么多个“有效按键间隔”才允许出分析结果，太少算出来不准
MIN_INTERVALS = 20

# 采集样例：界面把它显示给用户照着打一遍。
# 含空格、逗号、句号、换行、括号与引号，长度约 111 字符。
SAMPLE_TEXT = (
    "hello world, this is a typing sample.\n"
    "print(\"speed\", 42)  # code with 'quotes'\n"
    "def add(a, b):\n"
    "    return a + b\n"
)

# 能计入“字数”的按键类别（回车/退格不算字）
_TEXT_KINDS = ("char", "space", "punct")
# record / analyze_events 允许的类别
_VALID_KINDS = ("backspace", "enter", "space", "punct", "char")
# 中文标点也按“标点”处理
_PUNCT_CHARS = ",.;:!?，。；：！？"

# 过滤掉手抖（太快）和走神（太慢）的间隔，只保留正常的打字节奏
_MIN_GAP_MS = 10.0
_MAX_GAP_MS = 3000.0

# 各类别样本太少的门槛：不够就不建议改对应参数（保持现状）
_SPACE_MIN_SAMPLES = 4
_PUNCT_MIN_SAMPLES = 4
_NEWLINE_MIN_SAMPLES = 2


class HabitError(ValueError):
    """采集到的样本不足，无法计算打字习惯。"""


def classify_key(char: str, keysym: str = "") -> str:
    """把一次按键归到统计用的类别；返回 "" 表示这次按键不参与统计。"""
    if keysym == "BackSpace":
        return "backspace"
    if keysym in ("Return", "KP_Enter"):
        return "enter"
    text = char or ""
    if text == " ":
        return "space"
    if len(text) == 1 and text in _PUNCT_CHARS:
        return "punct"
    # 单字符且可打印（不是 \r \n \t 这类控制字符）才算普通字符
    if len(text) == 1 and text not in ("\r", "\n") and text.isprintable():
        return "char"
    return ""


def _to_float(value: Any, default: float = 0.0) -> float:
    """宽松转 float；失败或不是有限数时返回默认值（不让脏数据炸掉整个分析）。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _profile_number(profile: Dict[str, Any], key: str, default: float) -> float:
    if not isinstance(profile, dict):
        return default
    return _to_float(profile.get(key), default)


def _profile_optional(profile: Dict[str, Any], key: str) -> Optional[float]:
    """取 profile 里的可空数值；没采到这类样本时是 None，表示“不返回该键”。"""
    if not isinstance(profile, dict) or profile.get(key) is None:
        return None
    number = _to_float(profile.get(key), float("nan"))
    return None if math.isnan(number) else number


def _clamp_int(value: Any, low: float, high: float, default: float = 0.0) -> int:
    """先四舍五入再夹到 [low, high]，保证返回 int。"""
    number = _to_float(value, default)
    upper = max(low, high)
    return int(round(max(low, min(upper, number))))


def _median(values: List[float]) -> float:
    return float(statistics.median(values)) if values else 0.0


def _percentile(values: List[float], percent: float) -> float:
    """线性插值分位数（和 numpy 默认算法一致，便于解释与复现）。"""
    ordered = sorted(values)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * percent / 100.0
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return float(ordered[low])
    ratio = position - low
    return float(ordered[low] + (ordered[high] - ordered[low]) * ratio)


class HabitProfiler:
    """记录按键时间戳，统计打字节奏。只存时间与类别，不存用户打的内容。"""

    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    def reset(self) -> None:
        """清空已采集的按键。"""
        self.events = []

    def _push(self, timestamp_ms: Any, kind: str) -> None:
        moment = _to_float(timestamp_ms, float("nan"))
        if math.isnan(moment):
            return
        self.events.append({"t": moment, "kind": kind})

    def record(self, timestamp_ms: float, char: str = "", keysym: str = "") -> None:
        """记录一次按键；不参与统计的按键（Shift、方向键等）直接忽略。"""
        kind = classify_key(char or "", keysym or "")
        if kind:
            self._push(timestamp_ms, kind)

    @property
    def char_count(self) -> int:
        """space / punct / char 的总数（不含回车、退格）。"""
        return sum(1 for event in self.events if event.get("kind") in _TEXT_KINDS)

    def _text_events(self) -> List[Dict[str, Any]]:
        return [event for event in self.events if event.get("kind") in _TEXT_KINDS]

    @property
    def intervals(self) -> List[float]:
        """相邻可打印按键之间的间隔（ms），只保留 10~3000ms 的正常节奏。"""
        gaps: List[float] = []
        previous: Optional[float] = None
        for event in self._text_events():
            moment = float(event.get("t", 0.0))
            if previous is not None:
                gap = moment - previous
                if _MIN_GAP_MS <= gap <= _MAX_GAP_MS:
                    gaps.append(gap)
            previous = moment
        return gaps

    @property
    def interval_count(self) -> int:
        return len(self.intervals)

    def _kind_times(self, kind: str) -> List[float]:
        return [float(event.get("t", 0.0)) for event in self.events if event.get("kind") == kind]

    def _category_extra(self, kind: str, median: float, min_samples: int) -> Optional[int]:
        """某类键（空格/标点/回车）相对整体中位间隔多停了多少 ms。"""
        gaps: List[float] = []
        previous: Optional[float] = None
        for event in self.events:
            moment = float(event.get("t", 0.0))
            if event.get("kind") == kind and previous is not None:
                gaps.append(moment - previous)
            previous = moment
        if len(gaps) < min_samples:
            return None
        return int(round(_median(gaps) - median))

    def _build_profile(self, intervals: List[float]) -> Dict[str, Any]:
        """根据有效间隔生成 profile。"""
        text_events = self._text_events()
        char_count = len(text_events)
        backspaces = self._kind_times("backspace")
        first = float(self.events[0].get("t", 0.0)) if self.events else 0.0
        last = float(self.events[-1].get("t", 0.0)) if self.events else 0.0
        seconds = max(0.0, last - first) / 1000.0

        median = _median(intervals)
        p10 = _percentile(intervals, 10)
        p90 = _percentile(intervals, 90)
        # 抖动 = 10~90 分位间隔跨度占中位间隔的百分比（越大说明节奏越不稳）
        jitter = ((p90 - p10) / median * 100.0) if median > 0 else 0.0
        cpm = int(round(60000.0 / median)) if median > 0 else 0
        typo_rate = round(min(1.0, len(backspaces) / float(char_count)), 4) if char_count else 0.0

        # 退格之间的间隔当作“发现打错到按退格”的节奏；只有一次退格算不出间隔
        backspace_gaps = [b - a for a, b in zip(backspaces, backspaces[1:]) if b >= a]
        fix_delay = int(round(_median(backspace_gaps))) if backspace_gaps else None

        note = ""
        if char_count < 60:
            note = "样本偏少（%d 字），结果仅供参考" % char_count

        return {
            "sample_chars": int(char_count),
            "sample_seconds": round(seconds, 2),
            "median_interval_ms": int(round(median)),
            "p10_interval_ms": int(round(p10)),
            "p90_interval_ms": int(round(p90)),
            "jitter_pct": int(round(jitter)),
            "chars_per_minute": int(cpm),
            "backspace_count": int(len(backspaces)),
            "typo_rate": typo_rate,
            "space_pause_ms": self._category_extra("space", median, _SPACE_MIN_SAMPLES),
            "punctuation_pause_ms": self._category_extra("punct", median, _PUNCT_MIN_SAMPLES),
            "newline_pause_ms": self._category_extra("enter", median, _NEWLINE_MIN_SAMPLES),
            "fix_delay_ms": fix_delay,
            "note": note,
        }

    def analyze(self, timing: Dict[str, Any] | None = None,
                human: Dict[str, Any] | None = None) -> Dict[str, Any]:
        """分析已采集的按键，返回 profile；有效间隔不足时抛 HabitError。

        timing / human 是预留的上下文参数（比如以后用于和当前设置做对比），
        目前统计口径固定为“只看采集到的按键”，不参与计算。
        """
        intervals = self.intervals
        if len(intervals) < MIN_INTERVALS:
            raise HabitError("有效的按键间隔只有 %d 个，至少需要 %d 个；请照着示例多打几行"
                            % (len(intervals), MIN_INTERVALS))
        return self._build_profile(intervals)


def analyze_events(events: List[Dict[str, Any]], timing: Dict[str, Any] | None = None,
                   human: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """离线分析一组事件：每项为 {"t": ms, "kind": ...} 或 {"t": ms, "char":..., "keysym":...}。"""
    profiler = HabitProfiler()
    for item in events or []:
        if not isinstance(item, dict):
            continue
        moment = item.get("t", 0.0)
        kind = item.get("kind")
        if kind in _VALID_KINDS:
            profiler._push(moment, str(kind))
        else:
            profiler.record(moment, char=str(item.get("char") or ""),
                            keysym=str(item.get("keysym") or ""))
    return profiler.analyze(timing, human)


def suggest(profile: Dict[str, Any], timing: Dict[str, Any],
            human: Dict[str, Any]) -> Dict[str, Any]:
    """按采集到的习惯给出要覆盖的配置项，键为 "section.key"。

    只返回有把握的参数：空格/标点/换行样本不足时保持现状，其它拟人参数
    （换位、连击、晚发现等）不动。
    """
    values: Dict[str, Any] = {}
    median = _profile_number(profile, "median_interval_ms", 55.0)
    p10 = _profile_number(profile, "p10_interval_ms", 30.0)
    p90 = _profile_number(profile, "p90_interval_ms", 120.0)
    jitter = _profile_number(profile, "jitter_pct", 45.0)
    sample_chars = _profile_number(profile, "sample_chars", 0.0)
    backspace_count = int(_profile_number(profile, "backspace_count", 0.0))

    # 基础节奏：中位间隔当每字间隔，10~90 分位当快慢两端
    char_delay = _clamp_int(round(median), 20, 220)
    burst_fast = _clamp_int(round(p10), 15, max(15, char_delay))
    burst_slow = _clamp_int(round(p90), burst_fast + 15, 500)
    values["timing.char_delay_ms"] = char_delay
    values["timing.char_delay_jitter_pct"] = _clamp_int(round(jitter), 5, 80)
    values["timing.burst_mode"] = True
    values["timing.burst_fast_ms"] = burst_fast
    values["timing.burst_slow_ms"] = burst_slow

    # 分类停顿：只有样本够了才动（否则保持用户现在的设置）
    space_pause = _profile_optional(profile, "space_pause_ms")
    if space_pause is not None:
        values["timing.space_pause_ms"] = _clamp_int(round(space_pause), 0, 300)
    punctuation_pause = _profile_optional(profile, "punctuation_pause_ms")
    if punctuation_pause is not None:
        values["timing.punctuation_pause_ms"] = _clamp_int(round(punctuation_pause), 0, 600)
    newline_pause = _profile_optional(profile, "newline_pause_ms")
    if newline_pause is not None:
        values["timing.newline_pause_ms"] = _clamp_int(round(newline_pause), 0, 1500)

    # 打错率：样本够 40 个字才可信
    if sample_chars >= 40:
        typo_rate = 0.0
        if sample_chars > 0:
            typo_rate = backspace_count / sample_chars
        values["human.typo_rate"] = round(max(0.0, min(0.2, typo_rate)), 4)

    # 改错节奏：至少要有两次退格才能量出“两次退格之间的间隔”
    if backspace_count >= 2:
        gap = _profile_optional(profile, "fix_delay_ms")
        if gap is None:
            gap = median
        fix_min = _clamp_int(round(gap * 0.6), 60, 2000)
        fix_max = _clamp_int(round(gap * 1.8), fix_min + 80, 3000)
        values["human.fix_delay_min_ms"] = fix_min
        values["human.fix_delay_max_ms"] = fix_max

    return values


def summarize(profile: Dict[str, Any]) -> str:
    """一句话概括采集结果，给界面直接显示。"""
    median = int(round(_profile_number(profile, "median_interval_ms", 0.0)))
    cpm = int(round(_profile_number(profile, "chars_per_minute", 0.0)))
    jitter = int(round(_profile_number(profile, "jitter_pct", 0.0)))
    chars = int(round(_profile_number(profile, "sample_chars", 0.0)))
    backspaces = int(round(_profile_number(profile, "backspace_count", 0.0)))
    return "中位间隔 %d ms · 约 %d 字/分 · 抖动 %d%% · 样本 %d 字 · 退格 %d 次" % (
        median, cpm, jitter, chars, backspaces)


def profile_to_config(profile: Dict[str, Any]) -> Dict[str, Any]:
    """挑出可以安全写进 config["habits"] 的数值字段（JSON 安全）。"""
    return {
        "sample_chars": int(round(_profile_number(profile, "sample_chars", 0.0))),
        "sample_seconds": round(max(0.0, _profile_number(profile, "sample_seconds", 0.0)), 2),
        "median_interval_ms": int(round(_profile_number(profile, "median_interval_ms", 0.0))),
        "jitter_pct": int(round(_profile_number(profile, "jitter_pct", 0.0))),
        "chars_per_minute": int(round(_profile_number(profile, "chars_per_minute", 0.0))),
        "typo_rate": round(max(0.0, min(0.2, _profile_number(profile, "typo_rate", 0.0))), 4),
    }
