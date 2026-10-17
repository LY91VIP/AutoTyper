# -*- coding: utf-8 -*-
r"""路径定位——保证程序“整目录可搬迁”。

可写数据默认放在程序目录旁边（绿色便携）；程序目录不可写时回退到用户目录。
打包成 exe 后以 exe 所在目录为准（而不是解包临时目录）。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

APP_DIR_NAME = "AutoTyperStandalone"

#: 包所在目录
PKG_DIR = os.path.dirname(os.path.abspath(__file__))


def _program_root() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(PKG_DIR)


#: 程序根目录（含 AutoTyper.pyw 或 打字助手.exe 的那一层）
PROGRAM_DIR = _program_root()


def _is_writable(directory: str) -> bool:
    try:
        os.makedirs(directory, exist_ok=True)
        fd, probe = tempfile.mkstemp(prefix=".probe-", dir=directory)
        os.close(fd)
        os.remove(probe)
        return True
    except Exception:
        return False


def default_data_dir() -> str:
    override = os.environ.get("AUTOTYPER_DATA_DIR")
    if override:
        os.makedirs(override, exist_ok=True)
        return override
    if _is_writable(PROGRAM_DIR):
        return PROGRAM_DIR
    fallback = os.path.join(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(),
                            APP_DIR_NAME)
    os.makedirs(fallback, exist_ok=True)
    return fallback


DATA_DIR = default_data_dir()
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
STATE_PATH = os.path.join(DATA_DIR, "state.json")
LOG_DIR = os.path.join(DATA_DIR, "logs")
LOG_PATH = os.path.join(LOG_DIR, "autotyper.log")
SCRIPTS_DIR = os.path.join(PROGRAM_DIR, "scripts")
ASSETS_DIR = os.path.join(PROGRAM_DIR, "assets")
ICON_PATH = os.path.join(ASSETS_DIR, "icon.ico")


def _relative_inside(path: str, root: str) -> str:
    """path 在 root 里面时返回相对路径（"." 表示就是 root），否则返回空字符串。"""
    if not root:
        return ""
    try:
        rel = os.path.relpath(os.path.abspath(path), os.path.abspath(root))
    except Exception:
        return ""
    if rel == os.curdir:
        return "."
    if os.path.isabs(rel) or rel == os.pardir or rel.startswith(os.pardir + os.sep):
        return ""
    return rel


def display_path(path: str) -> str:
    """给日志/提示用的简短路径，不暴露用户名和完整目录。

    在程序目录里显示相对路径（如 config.json）；在 %LOCALAPPDATA% 里用
    %LOCALAPPDATA%\\... 表示；其他位置只显示文件名。
    """
    if not path:
        return ""
    try:
        absolute = os.path.abspath(path)
    except Exception:
        return str(path)
    rel = _relative_inside(absolute, PROGRAM_DIR)
    if rel:
        return rel
    local = os.environ.get("LOCALAPPDATA") or ""
    rel = _relative_inside(absolute, local)
    if rel:
        return "%LOCALAPPDATA%\\" + rel
    return os.path.basename(absolute)


def describe_path(path: str) -> str:
    """界面用的“文件名（所在位置）”，例如 config.json（程序目录）。"""
    if not path:
        return ""
    try:
        absolute = os.path.abspath(path)
    except Exception:
        return str(path)
    name = os.path.basename(absolute)
    folder = os.path.dirname(absolute)
    rel = _relative_inside(folder, PROGRAM_DIR)
    if rel:
        where = "程序目录" if rel == os.curdir else rel
        return "%s（%s）" % (name, where)
    local = os.environ.get("LOCALAPPDATA") or ""
    rel = _relative_inside(folder, local)
    if rel:
        return "%s（%%LOCALAPPDATA%%\\%s）" % (name, rel)
    return name


def resolve_script(name: str) -> str:
    if not name:
        return ""
    if os.path.isabs(name):
        return name
    candidate = os.path.join(SCRIPTS_DIR, name)
    if os.path.exists(candidate):
        return candidate
    beside = os.path.join(PROGRAM_DIR, name)
    if os.path.exists(beside):
        return beside
    if os.path.exists(name):
        return os.path.abspath(name)
    return candidate


def ensure_dirs() -> None:
    for folder in (LOG_DIR, SCRIPTS_DIR, ASSETS_DIR):
        try:
            os.makedirs(folder, exist_ok=True)
        except Exception:
            pass


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _resource_dir() -> str:
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", PROGRAM_DIR)
    return PROGRAM_DIR


def seed_bundled_files() -> None:
    """首次运行时把打包进去的示例脚本/图标复制到程序目录旁边。"""
    resource = _resource_dir()
    for sub in ("scripts", "assets"):
        source_dir = os.path.join(resource, sub)
        if not os.path.isdir(source_dir):
            continue
        target_dir = os.path.join(PROGRAM_DIR, sub)
        try:
            os.makedirs(target_dir, exist_ok=True)
            for name in os.listdir(source_dir):
                src = os.path.join(source_dir, name)
                dst = os.path.join(target_dir, name)
                if os.path.isfile(src) and not os.path.exists(dst):
                    shutil.copy2(src, dst)
        except Exception:
            pass
