#!/usr/bin/env python3
"""从 Agent 已检出的最新稳定 Release 离线升级书架，零第三方依赖。"""
import argparse
from pathlib import Path
import re
import sys

from init_library import SKILL_ROOT, install_app


def read_version(path):
    value = path.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError(f"无效版本号：{path}")
    return value


def update_workbench(target):
    target = target.expanduser().resolve()
    if not target.is_dir() or not (target / "小说台.html").is_file():
        raise ValueError("未找到已初始化书架的 小说台.html，请确认书架目录；不会新建书架。")
    new = read_version(SKILL_ROOT / "VERSION")
    version_file = target / "VERSION"
    if version_file.is_symlink():
        raise ValueError("拒绝读取软链接 VERSION")
    old = read_version(version_file) if version_file.exists() else "未知（旧版无 VERSION）"
    print(f"升级前版本：{old}；目标稳定版：{new}")
    if old == new:
        print(f"已是最新 {new}，无需升级；没有创建备份。")
        return
    if version_file.exists() and tuple(map(int, old.split('.'))) > tuple(map(int, new.split('.'))):
        raise ValueError("本地版本高于当前仓库，拒绝降级；请让 Agent 获取最新稳定 Release。")
    # Same allowlist and backup path as init_library.py --update-app;
    # never initialize templates, rules or example manuscripts during upgrades.
    install_app(target, update_app=True)
    print(f"升级后版本：{read_version(version_file)}")
    print("小说正文、作者规则和已有模板没有被覆盖。")
    print("若该书架服务正在运行，请由 Agent 按原启动方式重启并验证。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target_dir", type=Path, help="已经初始化的书架目录")
    args = parser.parse_args()
    try:
        update_workbench(args.target_dir)
    except (OSError, ValueError) as exc:
        print(f"升级失败：{exc}。正文、规则和模板未被操作；如应用文件部分更新，请检查 _workbench-backups/ 后重试。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
