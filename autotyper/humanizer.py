# -*- coding: utf-8 -*-
"""拟人化引擎：把一段文本翻译成“带时序的击键动作”。

纯逻辑，不碰 Windows API，可离线测试。建模三件事：
  1. 节奏不均匀（抖动 + 快慢交替 burst）；
  2. 会打错（邻键错字 / 相邻打反 / 连击重复）；
  3. 发现错误有延迟（有时多打几个字符才回头删）。
"""

from __future__ import annotations

import random
from typing import Any, Dict, Iterator, List, Optional, Tuple

NEIGHBORS: Dict[str, str] = {
    "q": "wa", "w": "qeas", "e": "wrsd", "r": "etdf", "t": "ryfg", "y": "tugh",
    "u": "yijh", "i": "uojk", "o": "ipkl", "p": "ol",
    "a": "qwsz", "s": "awedxz", "d": "serfcx", "f": "drtgvc", "g": "ftyhbv",
    "h": "gyujnb", "j": "huikmn", "k": "jiolm", "l": "kop",
    "z": "asx", "x": "zsdc", "c": "xdfv", "v": "cfgb", "b": "vghn",
    "n": "bhjm", "m": "njk",
    "1": "2q", "2": "13qw", "3": "24we", "4": "35er", "5": "46rt",
    "6": "57ty", "7": "68yu", "8": "79ui", "9": "80io", "0": "9op",
    ",": "mkl.", ".": ",l/", ";": "l'p", "'": ";[/", "[": "p]", "]": "[\\",
    "-": "0=", "=": "-]", "/": ".;", "\\": "][",
}

LOOKALIKE = {"_": "-", "-": "_", "0": "o", "o": "0", "1": "l", "l": "1", "i": "1"}


class Action:
    __slots__ = ("kind", "text", "key", "delay_ms", "progress_index", "plan_id")

    def __init__(self, kind: str, delay_ms: int = 0, text: str = "", key: str = ""):
        self.kind = kind
        self.delay_ms = max(0, int(delay_ms))
        self.text = text
        self.key = key
        self.progress_index = 0
        self.plan_id = None

    def __repr__(self) -> str:  # pragma: no cover
        body = self.text if self.kind == "text" else (self.key if self.kind == "key" else "%dms" % self.delay_ms)
        return "<Action %s %r +%dms>" % (self.kind, body, self.delay_ms)


def ch_ok(ch: str) -> bool:
    return bool(ch) and not ch.isspace()


class BurstRhythm:
    """快慢交替的节奏。"""

    def __init__(self, owner: "Humanizer"):
        self.owner = owner
        self.remaining = 0
        self.current = 0.0

    def _roll(self) -> None:
        t = self.owner.t
        if not t.get("burst_mode", True):
            self.remaining = 1
            self.current = self.owner._num(t, "char_delay_ms", 55)
            return
        low = int(self.owner._num(t, "burst_len_min", 6))
        high = int(self.owner._num(t, "burst_len_max", 22))
        self.remaining = max(1, self.owner.rng.randint(low, max(low, high)))
        fast = self.owner._num(t, "burst_fast_ms", 26)
        slow = self.owner._num(t, "burst_slow_ms", 95)
        self.current = fast if self.owner.rng.random() < 0.5 else self.owner.rng.uniform(min(fast, slow), max(fast, slow))

    def next_delay(self, extra: int = 0) -> int:
        if self.remaining <= 0:
            self._roll()
        self.remaining -= 1
        jitter = max(0.0, min(1.0, self.owner._num(self.owner.t, "char_delay_jitter_pct", 45) / 100.0))
        delay = self.current * self.owner.rng.uniform(1.0 - jitter, 1.0 + jitter) + extra
        chance = max(0.0, min(1.0, self.owner._num(self.owner.t, "thinking_pause_chance", 0.02)))
        if self.owner.rng.random() < chance:
            low = int(self.owner._num(self.owner.t, "thinking_pause_min_ms", 180))
            high = max(low, int(self.owner._num(self.owner.t, "thinking_pause_max_ms", 700)))
            delay += self.owner.rng.randint(low, high)
        return int(max(1.0, delay))


class Humanizer:
    def __init__(self, timing: Dict[str, Any], human: Dict[str, Any],
                 rng: Optional[random.Random] = None):
        self.t = timing or {}
        self.h = human or {}
        self.rng = rng or random.Random()

    def _num(self, section: Dict[str, Any], key: str, default: float) -> float:
        try:
            return float(section.get(key, default))
        except (TypeError, ValueError):
            return float(default)

    def _rhythm(self) -> BurstRhythm:
        return BurstRhythm(self)

    def _extra_for(self, ch: str) -> int:
        if ch == " ":
            return int(self._num(self.t, "space_pause_ms", 25))
        if ch in ",.;:!?，。；：！？":
            return int(self._num(self.t, "punctuation_pause_ms", 130))
        return 0

    # ------------------------------------------------------------------ 主流程
    def actions(self, text: str, newline_mode: str = "enter") -> Iterator[Action]:
        rhythm = self._rhythm()
        typo_done: set = set()
        plan_counter = 0
        index = 0
        length = len(text)

        while index < length:
            ch = text[index]
            if ch == "\r":
                index += 1
                continue
            if ch == "\n":
                extra = self._num(self.t, "newline_pause_ms", 200)
                if newline_mode == "unicode":
                    action = Action("text", rhythm.next_delay(extra), text="\n")
                else:
                    action = Action("key", rhythm.next_delay(extra), key="enter")
                action.progress_index = index + 1
                yield action
                index += 1
                continue

            if index not in typo_done:
                typo_done.add(index)
                planned = self._plan_typo(text, index)
                if planned:
                    plan, final_index = planned
                    plan_counter += 1
                    last = len(plan) - 1
                    for position, action in enumerate(plan):
                        action.plan_id = plan_counter
                        action.progress_index = final_index if position == last else index
                        yield action
                    index = max(index, final_index)
                    continue

            action = Action("text", rhythm.next_delay(self._extra_for(ch)), text=ch)
            action.progress_index = index + 1
            yield action
            index += 1

    # ------------------------------------------------------------------ 错字
    def _plan_typo(self, text: str, index: int) -> Optional[Tuple[List[Action], int]]:
        rhythm = self._rhythm()
        rate = max(0.0, min(1.0, self._num(self.h, "typo_rate", 0.025)))
        if self.rng.random() >= rate:
            return None
        transpose_rate = max(0.0, self._num(self.h, "transpose_rate", 0.008))
        double_rate = max(0.0, self._num(self.h, "double_char_rate", 0.006))
        fix_delay = self.rng.randint(
            int(self._num(self.h, "fix_delay_min_ms", 120)),
            max(int(self._num(self.h, "fix_delay_min_ms", 120)),
                int(self._num(self.h, "fix_delay_max_ms", 420))))
        skip_fix = self.rng.random() < max(0.0, min(1.0, self._num(self.h, "skip_fix_rate", 0.0)))
        subtype = self.rng.random()

        # 相邻两个字打反
        if subtype < transpose_rate / max(rate, 1e-9) and index + 1 < len(text):
            first, second = text[index], text[index + 1]
            if ch_ok(first) and ch_ok(second):
                plan = [Action("text", rhythm.next_delay(), text=second),
                        Action("text", rhythm.next_delay(), text=first)]
                if skip_fix:
                    return plan, index + 2
                plan.append(Action("wait", fix_delay))
                plan.append(Action("key", 40, key="backspace"))
                plan.append(Action("key", 40, key="backspace"))
                return plan, index

        # 同一个键连击两下
        if subtype < (transpose_rate + double_rate) / max(rate, 1e-9) and ch_ok(text[index]):
            plan = [Action("text", rhythm.next_delay(), text=text[index]),
                    Action("text", rhythm.next_delay(), text=text[index])]
            if skip_fix:
                return plan, index + 1
            plan.append(Action("wait", fix_delay))
            plan.append(Action("key", 40, key="backspace"))
            return plan, index + 1

        # 按错键
        wrong = self._wrong_char(text[index])
        if not wrong:
            return None
        extra = 0
        late_rate = max(0.0, min(1.0, self._num(self.h, "late_notice_rate", 0.25)))
        if (not skip_fix) and index + 1 < len(text) and self.rng.random() < late_rate:
            extra = self.rng.randint(0, max(0, int(self._num(self.h, "late_notice_extra_max", 3))))
            extra = min(extra, len(text) - index - 1)
        plan: List[Action] = [Action("text", rhythm.next_delay(), text=wrong)]
        for offset in range(1, extra + 1):
            nxt = text[index + offset]
            if nxt == "\n":
                plan.append(Action("key", rhythm.next_delay(), key="enter"))
            elif nxt != "\r":
                plan.append(Action("text", rhythm.next_delay(), text=nxt))
        if skip_fix:
            return plan, index + extra + 1
        plan.append(Action("wait", fix_delay))
        for _ in range(extra + 1):
            plan.append(Action("key", 35, key="backspace"))
        return plan, index

    def _wrong_char(self, ch: str) -> str:
        if not ch_ok(ch):
            return ""
        neighbor_ratio = max(0.0, min(1.0, self._num(self.h, "neighbor_typo_ratio", 0.8)))
        lowered = ch.lower()
        if self.rng.random() < neighbor_ratio:
            pool = NEIGHBORS.get(lowered, "")
            if pool:
                picked = self.rng.choice(pool)
                return picked.upper() if ch.isupper() else picked
        if lowered in LOOKALIKE and self.rng.random() < 0.4:
            return LOOKALIKE[lowered]
        candidates = "abcdefghijklmnopqrstuvwxyz0123456789"
        picked = self.rng.choice(candidates)
        return picked.upper() if ch.isupper() else picked
