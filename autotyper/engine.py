# -*- coding: utf-8 -*-
"""打字引擎：把“文本 + 分段”变成一段段拟人化的真实输入。

设计要点：
  * 动作按“计划（plan）”分组，一个错字计划要么整段做完、要么整段不做，
    所以按 ↑ 停止时不会留下半截错字；
  * 位置只在计划边界推进，续打绝不重复/丢字；
  * 打字中途前台窗口被切走会立刻停下（防止把内容打进别的程序）。
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from . import paths
from .config import Config
from .humanizer import Humanizer
from .win32_input import SendInputSink, foreground_info

IDLE = "idle"
TYPING = "typing"
DONE = "done"
STOPPED = "stopped"

IDE_PROCESSES = (
    "pycharm", "idea", "webstorm", "clion", "goland", "rider", "phpstorm",
    "android studio", "code.exe", "code - insiders", "sublime_text", "notepad++",
    "devenv", "eclipse", "xcode",
)


# ------------------------------------------------------------------ 文本处理
def resolve_indent_mode(mode: str, process_name: str = "") -> str:
    mode = (mode or "auto").lower()
    if mode in ("keep", "strip", "relative"):
        return mode
    name = (process_name or "").lower()
    if any(keyword in name for keyword in IDE_PROCESSES):
        return "strip"
    return "keep"


def apply_indent(text: str, mode: str) -> str:
    mode = (mode or "keep").lower()
    if mode == "strip":
        lines = [line.lstrip(" \t") for line in text.split("\n")]
        return "\n".join(lines)
    if mode == "relative":
        lines = text.split("\n")
        best = None
        for line in lines:
            if not line.strip():
                continue
            indent = len(line) - len(line.lstrip(" \t"))
            best = indent if best is None else min(best, indent)
        if not best:
            return text
        result = []
        for line in lines:
            if not line.strip():
                result.append(line)
            else:
                result.append(line[best:])
        return "\n".join(result)
    return text


def build_segments(text: str, mode: str = "line", chars_per_segment: int = 40) -> List[Tuple[int, int]]:
    if not text:
        return []
    mode = (mode or "line").lower()
    length = len(text)
    if mode == "all":
        return [(0, length)]
    segments: List[Tuple[int, int]] = []
    if mode == "chars":
        step = max(1, int(chars_per_segment or 40))
        start = 0
        while start < length:
            segments.append((start, min(start + step, length)))
            start += step
        return segments
    if mode == "block":
        start = 0
        index = 0
        while index < length:
            if text.startswith("\n\n", index):
                segments.append((start, index + 2))
                index += 2
                start = index
                continue
            index += 1
        if start < length:
            segments.append((start, length))
        return segments or [(0, length)]
    # line（默认）：每个 \n 结尾算一段，连续空行按段落合并
    start = 0
    for index, ch in enumerate(text):
        if ch == "\n":
            segments.append((start, index + 1))
            start = index + 1
    if start < length:
        segments.append((start, length))
    return segments or [(0, length)]


def segment_end_for(segments: List[Tuple[int, int]], position: int) -> int:
    """定位“包含 position 的那一段”的终点（越界时返回全文长度）。"""
    if not segments:
        return 0
    for start, end in segments:
        if start <= position < end:
            return end
    return segments[-1][1]


def segment_index_for(segments: List[Tuple[int, int]], position: int) -> int:
    for index, (start, end) in enumerate(segments):
        if start <= position < end:
            return index
    return len(segments)


def build_steps_layout(steps, indent_mode: str = "keep", trim_trailing: bool = False,
                       auto_enter: bool = True) -> Tuple[str, List[Tuple[int, int]], List[str]]:
    """把“楼层”拼成整段文本 + 分段表。

    空楼层（只有空白）直接跳过；auto_enter=True 时每层后面自动补一个换行。
    """
    cleaned = []
    for index, step in enumerate(steps or []):
        if isinstance(step, dict):
            name = str(step.get("name") or ("第 %d 层" % (index + 1)))
            body = str(step.get("text") or "")
        else:
            name = "第 %d 层" % (index + 1)
            body = str(step or "")
        if not body.strip():
            continue
        cleaned.append((name, apply_indent(body, indent_mode)))
    if not cleaned:
        return "", [], []
    pieces = []
    names = []
    for index, (name, body) in enumerate(cleaned):
        if trim_trailing:
            body = "\n".join(line.rstrip(" \t") for line in body.split("\n"))
        body = body.strip("\n")
        piece = body + ("\n" if (auto_enter and index < len(cleaned) - 1) else "")
        pieces.append(piece)
        names.append(name)
    text = "".join(pieces)
    segments = []
    cursor = 0
    for piece in pieces:
        segments.append((cursor, cursor + len(piece)))
        cursor += len(piece)
    return text, segments, names


# ------------------------------------------------------------------ 引擎
class TypingEngine:
    def __init__(self, cfg: Config, sink=None, on_event: Optional[Callable] = None,
                 logger: Optional[Callable[[str], None]] = None):
        self.cfg = cfg
        self.log = logger or (lambda message: None)
        self.on_event = on_event or (lambda *args, **kwargs: None)
        self.sink = sink or SendInputSink(self._input_mode())
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.position = 0
        self.state = IDLE
        self.last_message = ""
        self._layout_cache: Dict = {}
        self._text = ""
        self._segments: List[Tuple[int, int]] = []
        self._names: List[str] = []
        self._typing_started = 0.0
        self._refresh_layout()
        self.load_state()

    # ---------------- 布局缓存 ----------------
    def _input_mode(self) -> str:
        return str(self.cfg.get("behavior.input_mode", "auto") or "auto")

    def using_steps(self) -> bool:
        return str(self.cfg.get("source.mode", "steps") or "steps").lower() == "steps"

    def _mode(self) -> str:
        return str(self.cfg.get("source.segment_mode", "line") or "line").lower()

    def _indent_mode(self) -> str:
        process = ""
        try:
            process = str(self.sink.process_name() or "")
        except Exception:
            process = ""
        return resolve_indent_mode(str(self.cfg.get("source.indent_mode", "auto")), process)

    def _refresh_layout(self) -> None:
        mode = self._mode()
        auto_enter = bool(self.cfg.get("source.auto_enter", True))
        key = (mode, auto_enter, self._input_mode())
        if key in self._layout_cache:
            self._text, self._segments, self._names = self._layout_cache[key]
            return
        indent_mode = self._indent_mode()
        trim = bool(self.cfg.get("source.trim_trailing_whitespace", False))
        if self.using_steps():
            text, segments, names = build_steps_layout(self.cfg.steps(), indent_mode, trim, auto_enter)
        else:
            raw = self.cfg.read_script_text()
            text = apply_indent(raw, indent_mode)
            if trim:
                text = "\n".join(line.rstrip(" \t") for line in text.split("\n"))
            segments = build_segments(text, mode, int(self.cfg.get("source.chars_per_segment", 40)))
            names = []
        self._text, self._segments, self._names = text, segments, names
        self._layout_cache[key] = (text, segments, names)

    def reload_text(self, keep_position: bool = False) -> None:
        with self._lock:
            self._layout_cache.clear()
            self._refresh_layout()
            if not keep_position:
                self.position = 0
                self.state = IDLE
            if self.position > len(self._text):
                self.position = 0

    # ---------------- 状态 ----------------
    def total_chars(self) -> int:
        self._refresh_layout()
        return len(self._text)

    def percent(self) -> float:
        total = self.total_chars()
        if not total:
            return 0.0
        return min(100.0, max(0.0, 100.0 * self.position / total))

    def __len__(self) -> int:
        return self.total_chars()

    def current_floor_index(self) -> int:
        self._refresh_layout()
        return segment_index_for(self._segments, self.position)

    def status(self) -> dict:
        with self._lock:
            self._refresh_layout()
            total = len(self._text)
            segments = self._segments
            index = segment_index_for(segments, self.position)
            step_name = ""
            if self.using_steps() and index < len(self._names):
                step_name = self._names[index]
            return {
                "typing": bool(self._thread and self._thread.is_alive()),
                "state": self.state,
                "position": self.position,
                "total": total,
                "percent": self.percent(),
                "segment": min(index + 1, max(1, len(segments))),
                "total_segments": max(1, len(segments)),
                "step_name": step_name,
                "steps_mode": self.using_steps(),
                "message": self.last_message,
            }

    def segments_info(self) -> List[Tuple[int, int]]:
        self._refresh_layout()
        return list(self._segments)

    def step_summaries(self) -> List[dict]:
        self._refresh_layout()
        result = []
        for index, (start, end) in enumerate(self._segments):
            body = self._text[start:end]
            result.append({"index": index + 1,
                           "name": self._names[index] if index < len(self._names) else "第 %d 段" % (index + 1),
                           "chars": end - start,
                           "preview": body.replace("\n", "⏎")[:40]})
        return result

    def next_segment_text(self) -> Optional[dict]:
        with self._lock:
            self._refresh_layout()
            if not self._text or self.position >= len(self._text):
                return None
            index = segment_index_for(self._segments, self.position)
            if index >= len(self._segments):
                return None
            start, end = self._segments[index]
            return {"text": self._text[start:end],
                    "index": index + 1,
                    "total": len(self._segments),
                    "name": self._names[index] if index < len(self._names) else ""}

    def preview(self, only_next: bool = True) -> List[str]:
        """干跑预览（不按任何键）：把下一段要做的动作列出来。"""
        info = self.next_segment_text()
        lines: List[str] = []
        if not info:
            lines.append("已经全部打完，没有下一段了。")
            return lines
        lines.append("下一段：第 %d/%d 段%s — 共 %d 个字符"
                     % (info["index"], info["total"],
                        ("（" + info["name"] + "）") if info["name"] else "",
                        len(info["text"])))
        snapshot = self.cfg.clone()
        humanizer = Humanizer(snapshot.section("timing"), snapshot.section("human"))
        newline_mode = str(self.cfg.get("behavior.newline_mode", "enter") or "enter")
        count = 0
        typos = 0
        for action in humanizer.actions(info["text"], newline_mode):
            count += 1
            if action.plan_id is not None:
                typos += 1
            if count <= 4:
                if action.kind == "text":
                    shown = action.text.replace("\n", "⏎")
                elif action.kind == "key":
                    shown = "[%s]" % action.key
                else:
                    shown = "[停顿]"
                lines.append("  %-10s %4d ms  %s" % (action.kind, action.delay_ms, shown))
        lines.append("  … 共 %d 个动作，其中 %d 个属于错字/修正" % (count, typos))
        return lines

    # ---------------- 打字 ----------------
    def trigger(self) -> Tuple[bool, str]:
        with self._lock:
            self._refresh_layout()
            if self._thread and self._thread.is_alive():
                return False, "正在打字中，按 ↑ 可以先停下"
            if not self._text:
                return False, "还没有要打的内容：请先在设置里填“楼层”或选择脚本"
            if self.position >= len(self._text):
                self.state = DONE
                return False, "已经全部打完了：要重头打请点“重置进度”"
            info = foreground_info()
            if int(info.get("pid") or 0) == os.getpid():
                return False, "焦点还在本程序的窗口上：请先点到要打字的程序（例如 PyCharm）再按 ↓"
            stop_event = threading.Event()
            self._stop_event = stop_event
            self.state = TYPING
            self._typing_started = time.time()
            target_signature = (0, 0)
            try:
                target_signature = tuple(self.sink.foreground_signature() or (0, 0))
            except Exception:
                target_signature = (0, 0)
            self._thread = threading.Thread(target=self._worker, args=(stop_event, target_signature),
                                            name="autotyper-typing", daemon=True)
            self._thread.start()
            self.last_message = ""
            return True, "开始打第 %d 段" % (self.current_floor_index() + 1)

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=3.0)

    def reset(self) -> None:
        with self._lock:
            self.position = 0
            self.state = IDLE
            self.last_message = ""
            self.save_state()

    def _worker(self, stop_event: threading.Event, target_signature) -> None:
        try:
            start_delay = float(self.cfg.get("timing.start_delay_ms", 400) or 0) / 1000.0
            if start_delay > 0 and stop_event.wait(start_delay):
                self._finish(STOPPED)
                return
            with self._lock:
                self._refresh_layout()
                segments = list(self._segments)
                total = len(self._text)
                base = self.position
                end = segment_end_for(segments, base)
                text = self._text[base:end]
                target_pid = int(foreground_info().get("pid") or 0)
            if not text:
                self._finish(DONE)
                return
            # 开始打之前先松开还按着的修饰键（Shift/Ctrl/Alt/Win），
            # 否则注入的按键会和它们组合成快捷键，导致大小写错乱。
            if bool(self.cfg.get("safety.release_modifiers", True)):
                try:
                    released = self.sink.release_modifiers()
                except AttributeError:
                    released = []
                except Exception as exc:
                    released = []
                    self.log("释放修饰键失败：%s" % exc)
                if released:
                    self.log("开打前松开了一直按着的：%s（避免组合键）" % "、".join(released))
            pause_on_focus = bool(self.cfg.get("safety.pause_on_focus_change", True))
            limit = int(self.cfg.get("safety.max_chars_per_trigger", 0) or 0)
            newline_mode = str(self.cfg.get("behavior.newline_mode", "enter") or "enter")
            humanizer = Humanizer(self.cfg.section("timing"), self.cfg.section("human"))
            typed_in_segment = 0
            offset = 0
            groups: List[List] = []
            current_plan = object()
            for action in humanizer.actions(text, newline_mode):
                if (groups and action.plan_id is not None
                        and action.plan_id == current_plan):
                    groups[-1].append(action)
                    continue
                groups.append([action])
                current_plan = action.plan_id
            for group in groups:
                if stop_event.is_set():
                    break
                if limit and typed_in_segment >= limit:
                    break
                if pause_on_focus and target_pid:
                    now_pid = int(foreground_info().get("pid") or 0)
                    if now_pid != target_pid:
                        self.last_message = "检测到窗口切换，已自动停止（回到目标程序再按 ↓ 继续）"
                        self.log(self.last_message)
                        break
                group_progress = None
                for action in group:
                    # 计划组内不用 stop_event.wait：一旦开始一个错字计划，
                    # 必须整组做完，否则会在屏幕上留下半截错字/多余字符。
                    if action.delay_ms:
                        time.sleep(max(0, int(action.delay_ms)) / 1000.0)
                    try:
                        if action.kind == "text":
                            self.sink.type_text(action.text)
                        elif action.kind == "key":
                            self.sink.press_key(action.key)
                    except Exception as exc:
                        self.log("按键出错：%s" % exc)
                    if action.progress_index:
                        group_progress = action.progress_index
                if group_progress is not None:
                    offset = group_progress
                    typed_in_segment = group_progress
                    with self._lock:
                        self.position = base + offset
                    if limit and typed_in_segment >= limit:
                        break
            else:
                pass
            with self._lock:
                self.position = min(base + offset, total)
                finished_segment = self.position >= end or self.position >= total
                self.state = DONE if self.position >= total else (STOPPED if stop_event.is_set() else IDLE)
            if finished_segment:
                index = segment_index_for(segments, base)
                name = self._names[index] if index < len(self._names) else ""
                try:
                    self.on_event("segment_done", {"index": index + 1,
                                                   "total": len(segments),
                                                   "name": name,
                                                   "finished": self.position >= total})
                except Exception:
                    pass
            self.save_state()
        except Exception as exc:  # pragma: no cover
            self.log("打字线程异常：%s" % exc)
            self._finish(STOPPED)
        finally:
            with self._lock:
                self._thread = threading.current_thread() if False else None

    def _finish(self, state: str) -> None:
        with self._lock:
            self.state = state
            self.save_state()

    # ---------------- 进度持久化 ----------------
    def save_state(self) -> None:
        if not bool(self.cfg.get("source.resume_last_position", True)):
            return
        try:
            payload = {"script": self._text[:200] and str(self.cfg.get("source.script", "")),
                       "position": int(self.position),
                       "updated": time.strftime("%Y-%m-%d %H:%M:%S")}
            with open(paths.STATE_PATH, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False)
        except Exception:
            pass

    def load_state(self) -> None:
        if not bool(self.cfg.get("source.resume_last_position", True)):
            return
        try:
            with open(paths.STATE_PATH, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            position = int(payload.get("position") or 0)
            if 0 < position <= len(self._text):
                self.position = position
                self.state = DONE if position >= len(self._text) else IDLE
        except Exception:
            pass
