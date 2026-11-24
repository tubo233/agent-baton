#!/usr/bin/env python3
"""把 baton skill 装到本机各代理的 skill 目录里。

用法：
    python install.py                      # 装到检测到的所有代理（复制，不是链接）
    python install.py --profile <目录>      # 同时把个人配置（本机配置.md、profile.json）放进每个副本的 local/
    python install.py --targets claude,agents
    python install.py --dry-run            # 只显示会装到哪里
    python install.py --uninstall          # 删除本脚本装过的副本（只删带安装标记的）

目标目录：
    claude   ~/.claude/skills/        Claude Code（Grok Build 也会读这里）
    agents   ~/.agents/skills/        Codex、Grok Build 等遵循 .agents 约定的代理
    agy      ~/.gemini/config/skills/ Antigravity（agy 命令行和 IDE）
    gemini   ~/.gemini/skills/        Gemini CLI
    qwen     ~/.qwen/skills/          Qwen Code
    opencode ~/.config/opencode/skills/ OpenCode

再次运行就是更新：skill 文件全部换新；没给 --profile 时，保留已有的 local/ 个人配置。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "skill" / "baton"
MARKER = ".installed-by-baton.json"
PROFILE_FILES = ("本机配置.md", "profile.json")

TARGETS = {
    # 名字: (skill 目录, 用来判断这个代理装没装的目录)
    "claude": ("~/.claude/skills", "~/.claude"),
    "agents": ("~/.agents/skills", None),          # 通用约定目录，Codex 会读；总是安装
    "agy": ("~/.gemini/config/skills", "~/.gemini/antigravity"),
    "gemini": ("~/.gemini/skills", None),          # 只在 gemini 命令存在时装
    "qwen": ("~/.qwen/skills", "~/.qwen"),
    "opencode": ("~/.config/opencode/skills", "~/.config/opencode"),
}


def expand(p: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(p)))


def detected(name: str) -> bool:
    skill_dir, probe = TARGETS[name]
    if name == "agents":
        return True
    if name == "gemini":
        return shutil.which("gemini") is not None
    return probe is not None and expand(probe).exists()


def copy_skill(dest: Path, profile: Path | None, skill_name: str) -> str:
    keep_local = None
    if dest.exists():
        marker = dest / MARKER
        if not marker.exists():
            return f"跳过 {dest}：那里已有同名 skill，不是本脚本装的。先挪开它，或用 --name 换个名字。"
        if profile is None and (dest / "local").exists():
            keep_local = dest.parent / f".{skill_name}.local.keep"
            if keep_local.exists():
                shutil.rmtree(keep_local)
            shutil.copytree(dest / "local", keep_local)
        shutil.rmtree(dest)
    shutil.copytree(SOURCE, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if skill_name != "baton":
        skill_md = dest / "SKILL.md"
        text = skill_md.read_text(encoding="utf-8").replace("name: baton", f"name: {skill_name}", 1)
        skill_md.write_text(text, encoding="utf-8")
    if keep_local is not None:
        for f in keep_local.iterdir():
            target = dest / "local" / f.name
            if f.is_dir():
                shutil.copytree(f, target, dirs_exist_ok=True)
            else:
                shutil.copy2(f, target)
        shutil.rmtree(keep_local)
    copied = []
    if profile is not None:
        for name in PROFILE_FILES:
            src = profile / name
            if src.exists():
                shutil.copy2(src, dest / "local" / name)
                copied.append(name)
    (dest / MARKER).write_text(json.dumps({
        "source": str(SOURCE), "installed": dt.datetime.now().isoformat(timespec="seconds"),
        "profile": str(profile) if profile else None, "profile_files": copied,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    note = f"，个人配置：{', '.join(copied)}" if copied else ("，保留了原有个人配置" if keep_local else "")
    return f"已装到 {dest}{note}"


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--targets", help="逗号分隔：" + ",".join(TARGETS) + "；默认装到检测到的全部")
    ap.add_argument("--profile", help="个人配置所在目录（含 本机配置.md / profile.json）")
    ap.add_argument("--name", default="baton", help="装成的 skill 名字（默认 baton）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    args = ap.parse_args()

    names = args.targets.split(",") if args.targets else [n for n in TARGETS if detected(n)]
    unknown = [n for n in names if n not in TARGETS]
    if unknown:
        print(f"不认识的目标：{', '.join(unknown)}", file=sys.stderr)
        return 2
    profile = expand(args.profile) if args.profile else None
    if profile is not None and not profile.is_dir():
        print(f"个人配置目录不存在：{profile}", file=sys.stderr)
        return 2

    for name in names:
        dest = expand(TARGETS[name][0]) / args.name
        if args.dry_run:
            print(f"[{name}] 会{'删除' if args.uninstall else '安装到'} {dest}")
            continue
        if args.uninstall:
            if (dest / MARKER).exists():
                shutil.rmtree(dest)
                print(f"[{name}] 已删除 {dest}")
            else:
                print(f"[{name}] {dest} 不是本脚本装的，没动")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        print(f"[{name}] {copy_skill(dest, profile, args.name)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
