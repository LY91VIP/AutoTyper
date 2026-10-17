# -*- coding: utf-8 -*-
"""把源码打包成独立 exe（PyInstaller onedir，绿色便携）。

打包只带“干净”的文件：源码目录里的 __pycache__ / *.pyc 一律不进安装包，
因为 .pyc 会记录编译时源码所在的完整路径（可能包含开发机的用户名）。
"""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DIST = os.path.join(ROOT, "打包输出")
WORK = os.path.join(ROOT, ".build-tmp")
STAGE = os.path.join(ROOT, ".build-stage")
DATA_DIRS = ("scripts", "assets", "selfcheck")
ROOT_FILES = ("启动打字助手.vbs", "打开设置.vbs", "运行自测.bat", "使用说明.txt")
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache"}
SKIP_SUFFIXES = (".pyc", ".pyo")


def _copy_tree_clean(source: str, target: str) -> None:
    """复制目录，跳过 __pycache__ / *.pyc 等开发机残留。"""
    os.makedirs(target, exist_ok=True)
    for base, dirs, files in os.walk(source):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        rel = os.path.relpath(base, source)
        out_dir = target if rel == os.curdir else os.path.join(target, rel)
        os.makedirs(out_dir, exist_ok=True)
        for name in files:
            if name.endswith(SKIP_SUFFIXES):
                continue
            shutil.copy2(os.path.join(base, name), os.path.join(out_dir, name))


def _prune_development_files(root: str) -> None:
    """打包结果里不留 __pycache__ / *.pyc（它们会暴露开发机的源码路径）。"""
    for base, dirs, files in os.walk(root):
        for name in list(dirs):
            if name in SKIP_DIRS:
                shutil.rmtree(os.path.join(base, name), ignore_errors=True)
                dirs.remove(name)
        for name in files:
            if name.endswith(SKIP_SUFFIXES):
                try:
                    os.remove(os.path.join(base, name))
                except OSError:
                    pass


def _merge_tree(source: str, target: str) -> None:
    for name in os.listdir(source):
        src = os.path.join(source, name)
        dst = os.path.join(target, name)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)


def main() -> int:
    for folder in (DIST, WORK, STAGE):
        shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(DIST, exist_ok=True)
    # 先准备“干净”的数据目录，再交给 PyInstaller
    for folder in DATA_DIRS:
        _copy_tree_clean(os.path.join(ROOT, folder), os.path.join(STAGE, folder))
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--windowed",
        "--name", "打字助手",
        "--icon", os.path.join(ROOT, "assets", "icon.ico"),
        "--version-file", os.path.join(ROOT, "打包版本信息.txt"),
        "--distpath", DIST,
        "--workpath", WORK,
        "--specpath", WORK,
        "--add-data", os.path.join(STAGE, "scripts") + ";scripts",
        "--add-data", os.path.join(STAGE, "assets") + ";assets",
        "--add-data", os.path.join(STAGE, "selfcheck") + ";selfcheck",
        "--add-data", os.path.join(ROOT, "启动打字助手.vbs") + ";.",
        "--add-data", os.path.join(ROOT, "打开设置.vbs") + ";.",
        "--add-data", os.path.join(ROOT, "运行自测.bat") + ";.",
        "--add-data", os.path.join(ROOT, "使用说明.txt") + ";.",
        "-y",
        os.path.join(ROOT, "AutoTyper.pyw"),
    ]
    print("开始打包……")
    result = subprocess.run(args, cwd=ROOT)
    if result.returncode != 0:
        print("打包失败，退出码：%s" % result.returncode)
        return result.returncode
    target = os.path.join(DIST, "打字助手_打包版")
    os.makedirs(target, exist_ok=True)
    inner = os.path.join(DIST, "打字助手")
    if os.path.isdir(inner):
        _merge_tree(inner, target)
        shutil.rmtree(inner, ignore_errors=True)
    # 打包版里的启动脚本要指向 exe
    for name in ROOT_FILES:
        src = os.path.join(ROOT, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(target, name))
    # 自测脚本/示例/图标要放在程序目录（--selftest 会按这个路径去找）
    for folder in DATA_DIRS:
        _copy_tree_clean(os.path.join(STAGE, folder), os.path.join(target, folder))
    _prune_development_files(target)
    shutil.rmtree(STAGE, ignore_errors=True)
    total = 0
    count = 0
    for base, _dirs, files in os.walk(target):
        for name in files:
            count += 1
            try:
                total += os.path.getsize(os.path.join(base, name))
            except OSError:
                pass
    print("打包完成：%d 个文件，%.1f MB" % (count, total / 1048576.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
