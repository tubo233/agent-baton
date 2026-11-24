#!/usr/bin/env python3
"""扫描本机装了哪些代理、各有哪些模型，并检查榜单缓存和本机战绩。

用法：
    python scan_agents.py                 # 扫描并打印摘要，结果写到状态目录（默认 ~/.baton/）
    python scan_agents.py --host codex    # 指明当前中控是谁（猜不准时用）
    python scan_agents.py --quick         # 只找程序和版本，不列模型（不联网）
    python scan_agents.py --json          # 打印完整 JSON
    python scan_agents.py --no-write      # 只打印，不写文件（只读的测试或沙箱里用）

输出：<状态目录>/inventory.json 和 inventory.md。
只运行各代理的 --version 和列模型命令；不读任何凭证文件。
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

from common import (SECRET_NAMES, SKILL_NAME, detect_host, expand, find_executable, load_catalog,
                    load_json, load_profile, run_capture, state_dir, utf8_stdout)

ID_RE = re.compile(r"^[A-Za-z0-9][\w.\-/:]*$")
LEADERBOARD_MAX_AGE_DAYS = 14


def parse_models(kind: str, text: str) -> list[str]:
    models: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if kind == "grok_list":
            m = re.match(r"^[*\-]\s+(\S+)", line)
            if m:
                models.append(m.group(1))
        elif kind == "qwen_list":
            m = re.search(r"\(([\w.\-/:]+)\)", line)
            if m:
                models.append(m.group(1))
        else:  # first_column
            token = re.split(r"\s+", line)[0]
            if ID_RE.match(token) and not token.lower().startswith(("fetching", "warning", "error", "usage")):
                models.append(token)
    seen, out = set(), []
    for m in models:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def codex_models(path: str) -> tuple[list[str], dict]:
    """读 Codex 的模型缓存（不是凭证）。返回模型列表和每个模型支持的档位。"""
    p = Path(expand(path))
    if p.name in SECRET_NAMES:
        return [], {}
    data = load_json(p, {})
    models, efforts = [], {}
    for item in data.get("models", []) if isinstance(data, dict) else []:
        if not isinstance(item, dict) or item.get("visibility", "list") != "list":
            continue
        slug = item.get("slug")
        if not slug:
            continue
        models.append(slug)
        levels = [lv.get("effort") for lv in item.get("supported_reasoning_levels", []) if isinstance(lv, dict)]
        if levels:
            efforts[slug] = levels
    return models, efforts


def skill_installed(agent: dict) -> list[str]:
    hits = []
    for d in agent.get("skill_dirs", []):
        if (Path(expand(d)) / SKILL_NAME / "SKILL.md").exists():
            hits.append(d)
    return hits


def scan_agent(agent: dict, profile_agent: dict, quick: bool, previous: dict) -> dict:
    entry = {
        "id": agent["id"], "name": agent["name"], "vendor": agent.get("vendor"),
        "path": None, "version": None, "installed": False, "models": [], "model_efforts": {},
        "models_source": None, "login_hint": None, "headless_status": agent.get("headless", {}).get("status"),
        "skill_installed_in": [], "disabled_by_profile": bool(profile_agent.get("disabled")), "problems": [],
    }
    exe = find_executable(agent, profile_agent)
    if not exe:
        return entry
    entry["path"], entry["installed"] = exe, True
    code, text = run_capture([exe] + agent.get("version_args", ["--version"]), timeout=20)
    if code is None:
        entry["problems"].append("--version 超时")
    elif code != 0:
        entry["problems"].append(f"--version 退出码 {code}")
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    entry["version"] = first[:80] or None
    entry["skill_installed_in"] = skill_installed(agent)

    spec = agent.get("models", {})
    method = spec.get("method", "none")
    if profile_agent.get("models"):
        entry["models"], entry["models_source"] = list(profile_agent["models"]), "个人配置"
    elif method == "static":
        entry["models"], entry["models_source"] = list(spec.get("static", [])), "内置别名"
    elif method == "codex_cache":
        src = profile_agent.get("models_file") or spec.get("cache_file")
        entry["models"], entry["model_efforts"] = codex_models(src)
        entry["models_source"] = f"缓存文件 {src}"
        cache = load_json(Path(expand(spec.get("cache_file", ""))), {}) if spec.get("cache_file") else {}
        if isinstance(cache, dict) and cache.get("fetched_at"):
            entry["models_source"] += f"（Codex 缓存更新于 {cache['fetched_at'][:10]}）"
    elif method == "command" and not quick:
        code, text = run_capture([exe] + spec.get("args", []), timeout=60)
        if code is None:
            entry["problems"].append("列模型超时")
        elif code != 0:
            entry["problems"].append(f"列模型退出码 {code}：{text.strip()[:120]}")
        entry["models"] = parse_models(spec.get("parse", "first_column"), text)
        entry["models_source"] = "命令 " + " ".join(spec.get("args", []))
        low = text.lower()
        if "logged in" in low:
            entry["login_hint"] = "已登录"
        if "not authenticated" in low or "not logged in" in low:
            entry["login_hint"] = "未登录"
        if not entry["models"] and code == 0:
            entry["problems"].append("没有可用模型（可能未登录或没有额度）")
    elif method == "command":
        prev = previous.get(agent["id"], {})
        if prev.get("models") and prev.get("path") == exe:
            entry["models"] = list(prev["models"])
            entry["models_source"] = f"沿用 {prev.get('_scanned_at', '上次')} 的扫描（--quick 未重新列模型）"
        else:
            entry["models_source"] = "（--quick，未列模型）"
    exclude = set(profile_agent.get("models_exclude", []))
    entry["models"] = [m for m in entry["models"] if m not in exclude]
    return entry


def leaderboard_status(state: Path, all_models: list[str]) -> dict:
    data = load_json(state / "leaderboard.json", None)
    if not data:
        return {"exists": False, "updated": None, "age_days": None, "missing": all_models, "stale": True}
    updated = data.get("updated")
    age = None
    try:
        age = (dt.date.today() - dt.date.fromisoformat(str(updated)[:10])).days
    except Exception:
        pass
    known = set(data.get("checked_not_found") or [])  # 查过、榜单上没有的本机 ID，不算"缺"
    for key, item in (data.get("models") or {}).items():
        known.add(key)
        known.update(item.get("local_ids", []) if isinstance(item, dict) else [])
    missing = [m for m in all_models if m not in known]
    return {"exists": True, "updated": updated, "age_days": age, "missing": missing,
            "stale": age is None or age > LEADERBOARD_MAX_AGE_DAYS}


def history_summary(state: Path) -> list[dict]:
    path = state / "history.csv"
    if not path.exists():
        return []
    stats: dict[tuple, dict] = {}
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            key = (row.get("agent", ""), row.get("model", ""))
            s = stats.setdefault(key, {"agent": key[0], "model": key[1], "runs": 0, "ok": 0, "accepted": 0,
                                       "rejected": 0, "minutes": []})
            if row.get("event") == "finished":
                s["runs"] += 1
                if row.get("state") == "done":
                    s["ok"] += 1
                try:
                    s["minutes"].append(float(row.get("minutes") or 0))
                except ValueError:
                    pass
            elif row.get("event") == "review":
                if row.get("accepted") == "yes":
                    s["accepted"] += 1
                elif row.get("accepted") == "no":
                    s["rejected"] += 1
    out = []
    for s in stats.values():
        mins = sorted(s.pop("minutes"))
        s["median_minutes"] = round(mins[len(mins) // 2], 1) if mins else None
        out.append(s)
    return out


def render_md(inv: dict) -> str:
    lines = [f"# 本机代理清单（{inv['scanned_at']}）", ""]
    host = inv.get("host")
    lines.append(f"- 当前中控：{host or '未识别（请用 --host 指明）'}")
    lines.append(f"- 状态目录：{inv['state_dir']}")
    lines.append("")
    lines.append("| 代理 | 版本 | 无头调用 | 本 skill 已装 | 模型 | 问题 |")
    lines.append("|---|---|---|---|---|---|")
    for a in inv["agents"]:
        if not a["installed"]:
            continue
        tag = "（中控）" if a["id"] == host else ""
        status = {"verified": "已实测", "docs": "按文档，未实测"}.get(a["headless_status"], "—")
        if a["disabled_by_profile"]:
            status += "；个人配置已停用"
        models = ", ".join(a["models"][:30]) or "—"
        if len(a["models"]) > 30:
            models += f" 等 {len(a['models'])} 个"
        installed = "是" if a["skill_installed_in"] else "否"
        problems = "；".join(a["problems"]) or (a["login_hint"] or "")
        lines.append(f"| {a['name']}{tag} | {a['version'] or '—'} | {status} | {installed} | {models} | {problems} |")
    missing = [a["name"] for a in inv["agents"] if not a["installed"]]
    if missing:
        lines += ["", f"未找到：{', '.join(missing)}"]
    lb = inv["leaderboard"]
    lines += ["", "## 榜单缓存"]
    if not lb["exists"]:
        lines.append("- 还没有榜单缓存（leaderboard.json）。按 references/leaderboards.md 查一次。")
    else:
        lines.append(f"- 更新于 {lb['updated']}（{lb['age_days']} 天前）{'，已过期，建议重查' if lb['stale'] else ''}")
        if lb["missing"]:
            lines.append(f"- 缓存里没有的本机模型（{len(lb['missing'])} 个）：{', '.join(lb['missing'][:40])}")
    hist = inv["history"]
    lines += ["", "## 本机战绩"]
    if not hist:
        lines.append("- 还没有记录。")
    else:
        lines.append("| 代理 | 模型 | 跑完 | 正常结束 | 验收通过 | 验收不过 | 用时中位数（分钟） |")
        lines.append("|---|---|---|---|---|---|---|")
        for s in sorted(hist, key=lambda x: (x["agent"], x["model"])):
            lines.append(f"| {s['agent']} | {s['model']} | {s['runs']} | {s['ok']} | {s['accepted']} | {s['rejected']} | {s['median_minutes'] if s['median_minutes'] is not None else '—'} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", help="当前中控代理的 id（claude/codex/agy/grok/...）")
    ap.add_argument("--quick", action="store_true", help="不运行列模型命令")
    ap.add_argument("--json", action="store_true", help="打印 JSON 而不是摘要")
    ap.add_argument("--no-write", action="store_true", help="只打印，不写 inventory 文件（只读场合用）")
    args = ap.parse_args()

    catalog, profile = load_catalog(), load_profile()
    state = state_dir(profile)
    prof_agents = profile.get("agents", {})
    old = load_json(state / "inventory.json", {})
    previous = {a["id"]: {**a, "_scanned_at": old.get("scanned_at")} for a in old.get("agents", []) if "id" in a}
    agents = [scan_agent(a, prof_agents.get(a["id"], {}), args.quick, previous) for a in catalog.get("agents", [])
              if a.get("kind") != "test"]
    all_models = [m for a in agents if a["installed"] and not a["disabled_by_profile"] for m in a["models"]]
    inv = {
        "scanned_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "host": args.host or detect_host(),
        "host_network_disabled": os.environ.get("CODEX_SANDBOX_NETWORK_DISABLED") == "1",
        "state_dir": str(state),
        "profile_loaded": bool(profile),
        "agents": agents,
        "leaderboard": leaderboard_status(state, all_models),
        "history": history_summary(state),
    }
    md = render_md(inv)
    if inv["host_network_disabled"]:
        md += "\n> 注意：当前沙箱禁止联网。启动其他代理的命令需要申请在沙箱外运行，否则它们连不上自己的服务。\n"
    if not args.no_write:
        (state / "inventory.json").write_text(json.dumps(inv, ensure_ascii=False, indent=2), encoding="utf-8")
        (state / "inventory.md").write_text(md, encoding="utf-8")
    print(json.dumps(inv, ensure_ascii=False, indent=2) if args.json else md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
