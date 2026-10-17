# -*- coding: utf-8 -*-
"""配置读写：JSON + 默认值 + 容错合并 + 原子保存 + 自动备份（.bak）。"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from typing import Any, Dict

from . import paths

DEFAULTS: Dict[str, Any] = {
    "version": 1,
    "steps": [
        {"name": "第 1 层：打个招呼", "text": "print(\"你好，世界\")"},
        {"name": "第 2 层：循环三次", "text": "for i in range(3):\n    print(\"第\", i, \"次\")"},
        {"name": "第 3 层：收尾", "text": "print(\"完成\")"},
    ],
    "source": {
        "mode": "steps",
        "script": "示例_问候与排序.py.txt",
        "segment_mode": "line",
        "chars_per_segment": 40,
        "indent_mode": "auto",
        "trim_trailing_whitespace": False,
        "resume_last_position": True,
        "auto_enter": True,
    },
    "timing": {
        "start_delay_ms": 400,
        "char_delay_ms": 55,
        "char_delay_jitter_pct": 45,
        "newline_pause_ms": 200,
        "punctuation_pause_ms": 130,
        "space_pause_ms": 25,
        "thinking_pause_chance": 0.02,
        "thinking_pause_min_ms": 180,
        "thinking_pause_max_ms": 700,
        "burst_mode": True,
        "burst_fast_ms": 26,
        "burst_slow_ms": 95,
        "burst_len_min": 6,
        "burst_len_max": 22,
    },
    "human": {
        "typo_rate": 0.025,
        "neighbor_typo_ratio": 0.8,
        "transpose_rate": 0.008,
        "double_char_rate": 0.006,
        "fix_delay_min_ms": 120,
        "fix_delay_max_ms": 420,
        "late_notice_rate": 0.25,
        "late_notice_extra_max": 3,
        "skip_fix_rate": 0.0,
    },
    "hotkeys": {
        "enabled": True,
        "start_key": "down",
        "stop_key": "up",
        "panic_key": "f12",
        "require_modifier": "none",
        "swallow_keys": True,
        "swallow_mode": "target",
        "only_when_target": False,
        "target_process": "pycharm64.exe",
        "target_title_contains": "",
    },
    "safety": {
        "pause_on_focus_change": True,
        # 开始打字前自动松开 Shift/Ctrl/Alt/Win，避免组合键残留
        "release_modifiers": True,
        "max_chars_per_trigger": 0,
        "log_to_file": True,
    },
    "behavior": {
        "newline_mode": "enter",
        "input_mode": "auto",
        "start_hidden": True,
        # 点右上角 ×：False = 直接退出程序（默认，“关窗口=关程序”）
        #              True  = 只收进托盘，后台继续待命（⑤ 里可勾选）
        "close_to_tray": False,
        "show_tray_icon": True,
        "show_in_taskbar": True,
        "sound_on_done": True,
        "balloon_on_done": False,
        "autostart": False,
        "single_instance": True,
    },
    "lock_keys": {
        "enabled": False,
        "hold_unlock_key": "esc",
        "hold_unlock_seconds": 2.0,
    },
    "app_profiles": {},
    # 界面上的小偏好（比如楼层框拉多大）
    "ui": {"paned_ratio": 0.42},
}

_TRUE = ("1", "true", "yes", "on", "是", "开")
_FALSE = ("0", "false", "no", "off", "否", "关")


def _clean_steps(raw) -> list:
    result = []
    if isinstance(raw, list):
        for index, item in enumerate(raw):
            if isinstance(item, dict):
                result.append({"name": str(item.get("name") or ("第 %d 层" % (index + 1))),
                               "text": str(item.get("text") or "")})
            elif isinstance(item, str):
                result.append({"name": "第 %d 层" % (index + 1), "text": item})
    return result


def coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
    return default


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if key not in result:
            result[key] = value
            continue
        if isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        elif isinstance(result[key], bool):
            result[key] = coerce_bool(value, result[key])
        else:
            result[key] = value
    return result


def clamp(value: Any, low: float, high: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


class Config:
    def __init__(self, data: Dict[str, Any] | None = None, path: str | None = None):
        self.path = path or paths.CONFIG_PATH
        self.data = deep_merge(DEFAULTS, data or {})
        self.load_warnings: list = []

    # ---------- 读取 ----------
    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def section(self, name: str) -> Dict[str, Any]:
        value = self.data.get(name)
        return value if isinstance(value, dict) else {}

    def steps(self) -> list:
        return _clean_steps(self.data.get("steps"))

    def set_steps(self, steps: list) -> None:
        self.data["steps"] = _clean_steps(steps)

    def clone(self) -> "Config":
        return Config(copy.deepcopy(self.data), self.path)

    # ---------- 写入 ----------
    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        node = self.data
        for part in parts[:-1]:
            nxt = node.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                node[part] = nxt
            node = nxt
        node[parts[-1]] = value

    def to_json(self) -> str:
        return json.dumps(self.data, ensure_ascii=False, indent=2)

    def save(self) -> None:
        save_atomic(self.path, self.to_json())

    # ---------- 便捷 ----------
    @property
    def script_path(self) -> str:
        return paths.resolve_script(str(self.get("source.script", "")))

    def read_script_text(self) -> str:
        target = self.script_path
        try:
            with open(target, "r", encoding="utf-8-sig") as handle:
                return handle.read()
        except FileNotFoundError:
            return ""
        except Exception:
            try:
                with open(target, "r", encoding="gbk", errors="replace") as handle:
                    return handle.read()
            except Exception:
                return ""

    def effective_for(self, process_name: str) -> Dict[str, Any]:
        profiles = self.section("app_profiles")
        profile = profiles.get((process_name or "").lower())
        return profile if isinstance(profile, dict) else {}


def save_atomic(target: str, text: str) -> None:
    """原子写入；写之前把旧内容存成 .bak，防止一次覆盖就把设置弄丢。"""
    directory = os.path.dirname(os.path.abspath(target)) or "."
    os.makedirs(directory, exist_ok=True)
    try:
        if os.path.exists(target):
            with open(target, "r", encoding="utf-8-sig") as handle:
                previous = handle.read()
            if previous.strip() and previous != text:
                backup = target + ".bak"
                tmp_backup = backup + ".tmp"
                with open(tmp_backup, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(previous)
                os.replace(tmp_backup, backup)
    except Exception:
        pass
    fd, tmp = tempfile.mkstemp(prefix=".cfg-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp, target)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def load(path: str | None = None, create_if_missing: bool = True) -> Config:
    target = path or paths.CONFIG_PATH
    raw: Dict[str, Any] = {}
    if os.path.exists(target):
        try:
            with open(target, "r", encoding="utf-8-sig") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                raw = loaded
        except Exception as exc:
            raw = {}
    if not raw and os.path.exists(target + ".bak"):
        try:
            with open(target + ".bak", "r", encoding="utf-8-sig") as handle:
                backup = json.load(handle)
            if isinstance(backup, dict):
                raw = backup
        except Exception:
            pass
    cfg = Config(raw, target)
    if not raw and os.path.exists(target):
        cfg.load_warnings.append("配置文件解析失败，已尽量使用默认/备份配置")
    if create_if_missing and not os.path.exists(target):
        try:
            cfg.save()
        except Exception as exc:
            cfg.load_warnings.append("无法写入配置文件：%s" % exc)
    return cfg
