#!/usr/bin/env python3
"""派活、查进度、等待、停止、记录验收。中控会话用它启动其他代理的无头进程。

子命令：
  new-job  --name 任务名                      建任务文件夹（含 输入/ 和任务书模板）
  run      --job 目录 --agent id --model m   按 agent_catalog.json 拼命令并启动
           [--effort e] [--access job|workspace|job_shell] [--workdir 目录]
           [--add-dir 目录 ...] [--offline] [--extra 参数 ...] [--task-type 类型]
           [--foreground] [--dry-run]
  status   [--job 目录 ... | --root 目录]     列出任务状态和疑似失败原因
  wait     [--job 目录 ... | --root 目录] [--timeout 秒] [--interval 秒]
  stop     --job 目录                         连子进程一起停掉
  review   --job 目录 --accepted yes|no [--note 说明]   记下验收结果（进本机战绩）

每个任务文件夹里：启动提示.md（必需）、任务书.md、dispatch.json（启动参数）、
status.json（状态）、运行日志、运行错误.txt、运行时间.txt。
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from common import (IS_WIN, SCRIPT_DIR, expand, find_executable, kill_tree, load_catalog, load_json,
                    load_profile, state_dir, utf8_stdout)

HISTORY_FIELDS = ["time", "event", "job", "agent", "model", "effort", "task_type", "state", "exit_code",
                  "minutes", "accepted", "note"]
DEFAULT_SHORT_PROMPT = "请阅读当前目录下的 启动提示.md 并照做。"


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def append_history(profile: dict, row: dict) -> None:
    path = state_dir(profile) / "history.csv"
    new = not path.exists()
    with open(path, "a", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=HISTORY_FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in HISTORY_FIELDS})


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    if IS_WIN:
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(expand(path))).rstrip("\\/")


def check_forbidden(dirs: list[str], rules: list[dict]) -> str | None:
    for d in dirs:
        nd = norm(d)
        for rule in rules:
            rp = norm(rule["path"])
            if nd == rp or (rule.get("tree") and nd.startswith(rp + os.sep)):
                return f"{d} 命中个人配置的禁止目录 {rule['path']}（{'含子目录' if rule.get('tree') else '仅该目录'}）"
    return None


def find_agent(catalog: dict, agent_id: str) -> dict:
    for a in catalog.get("agents", []):
        if a["id"] == agent_id:
            return a
    raise SystemExit(f"目录表里没有代理 {agent_id}；可选：{', '.join(a['id'] for a in catalog.get('agents', []))}")


def build_command(args, agent: dict, prof_agent: dict, job: Path) -> tuple[list[str], dict]:
    exe = find_executable(agent, prof_agent)
    if not exe:
        raise SystemExit(f"找不到 {agent['name']} 的可执行文件；先跑 scan_agents.py，或在 local/profile.json 里写 path。")
    h = agent["headless"]
    access = args.access or "job"
    if access not in h.get("access", {}):
        raise SystemExit(f"{agent['name']} 不支持 access={access}；可选：{', '.join(h.get('access', {}))}")
    sp = h.get("short_prompt", DEFAULT_SHORT_PROMPT)
    if isinstance(sp, dict):
        sp = sp.get(access, sp.get("default", DEFAULT_SHORT_PROMPT))
    values = {
        "model": args.model, "effort": args.effort or "", "job": str(job),
        "workdir": str(Path(expand(args.workdir)).resolve()) if args.workdir else "",
        "prompt_file": str(job / "启动提示.md"), "last_message": str(job / "最后消息.md"), "short_prompt": sp,
    }

    def fill(items: list[str]) -> list[str]:
        out = []
        for item in items:
            if "{workdir}" in item and not values["workdir"]:
                raise SystemExit(f"access={access} 需要 --workdir")
            for k, v in values.items():
                item = item.replace("{" + k + "}", v)
            out.append(item)
        return out

    cmd = [exe] + fill(h.get("args", []))
    if args.effort:
        cmd += fill(h.get("effort_args", []))
    cmd += fill(h["access"][access])
    flag = h.get("add_dir_flag")
    for d in args.add_dir or []:
        if not flag:
            raise SystemExit(f"{agent['name']} 没有 add-dir 参数；把要读的文件复制进任务文件夹的 输入/。")
        cmd += [flag, str(Path(expand(d)).resolve())]
    if args.offline:
        cmd += h.get("offline_args", [])
    cmd += [expand(x) if x.startswith("~") else x for x in prof_agent.get("extra_args", [])]
    cmd += args.extra or []
    cmd += fill(h.get("trailing_args", []))
    info = {"exe": exe, "access": access, "prompt_via": h.get("prompt_via", "stdin"),
            "stderr_to_log": bool(h.get("stderr_to_log")),
            "cwd": str(job) if h.get("cwd", "job") == "job" or not values["workdir"] else values["workdir"],
            "log": h.get("log", "运行日志.txt"), "status_in_catalog": h.get("status")}
    return cmd, info


def classify(job: Path, agent: dict, meta: dict, failed: bool = True) -> str:
    """从日志尾部猜失败原因。只是提示，结论要看日志。正常结束的任务只查"空跑"。"""
    texts = []
    for name in (meta.get("log"), "运行错误.txt"):
        if not name:
            continue
        p = job / name
        if p.exists():
            with open(p, "rb") as fh:
                fh.seek(max(0, p.stat().st_size - 65536))
                texts.append(fh.read().decode("utf-8", errors="replace"))
    blob = "\n".join(texts)
    low = blob.lower()
    labels = {"quota": "额度或限流", "auth": "登录问题", "transient": "临时故障，可重试", "sandbox": "沙箱权限",
              "denied": "终端命令被自动拒绝（无人值守），运行可能空跑"}
    # "denied" 即使退出码为 0 也要报：agy 被拒后照样以成功状态退出。
    hits = [labels.get(kind, kind) for kind, pats in agent.get("failure_patterns", {}).items()
            if (failed or kind == "denied") and any(p.lower() in low for p in pats)]
    if agent["id"] == "agy" and '"response": ""' in blob.replace("\n", ""):
        hits.append("空跑（多半是终端命令被拒）")
    log = job / meta.get("log", "")
    if failed and meta.get("log") and log.exists() and log.stat().st_size < 200:
        hits.append("输出几乎为空")
    return "、".join(dict.fromkeys(hits))


# ---------------------------------------------------------------- 子命令

def cmd_new_job(args, profile: dict) -> int:
    root = Path(expand(args.root or profile.get("job_root") or str(state_dir(profile) / "jobs")))
    job = root / f"{dt.date.today().isoformat()}_{args.name}"
    (job / "输入").mkdir(parents=True, exist_ok=True)
    tpl = SCRIPT_DIR.parent / "references" / "brief-template.md"
    for name in ("任务书.md", "启动提示.md"):
        p = job / name
        if not p.exists():
            p.write_text(f"<!-- 按 {tpl} 的模板填写 -->\n", encoding="utf-8")
    print(job)
    return 0


def cmd_run(args, profile: dict) -> int:
    job = Path(expand(args.job)).resolve()
    prompt = job / "启动提示.md"
    status_path = job / "status.json"
    old = load_json(status_path, {})
    if old.get("state") == "running" and pid_alive(old.get("pid")):
        raise SystemExit(f"{job} 里已有任务在跑（pid {old.get('pid')}）。")
    catalog = load_catalog()
    agent = find_agent(catalog, args.agent)
    prof_agent = profile.get("agents", {}).get(args.agent, {})
    if prof_agent.get("disabled"):
        raise SystemExit(f"个人配置停用了 {agent['name']}：{prof_agent.get('notes', '')}")
    cmd, info = build_command(args, agent, prof_agent, job)
    rules = list(profile.get("forbidden_dirs", [])) + list(prof_agent.get("forbidden_dirs", []))
    problem = check_forbidden([info["cwd"]] + ([args.workdir] if args.workdir else []), rules)
    if problem:
        raise SystemExit("拒绝启动：" + problem)
    if not prompt.exists():
        raise SystemExit(f"{prompt} 不存在。先写好任务书和启动提示。")
    meta = {"agent": args.agent, "model": args.model, "effort": args.effort or "", "task_type": args.task_type or "",
            "cmd": cmd, **info, "created": now()}
    if args.dry_run:
        print(json.dumps(meta, ensure_ascii=False, indent=2))
        return 0
    if info["status_in_catalog"] != "verified":
        print(f"[提醒] {agent['name']} 的无头调用还没实测过，先确认这是一次小试。", file=sys.stderr)
    write_json(job / "dispatch.json", meta)
    write_json(status_path, {"state": "starting", "created": now()})
    if args.foreground:
        return supervise(job, profile)
    py = sys.executable
    kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(job))
    if IS_WIN:
        base = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            proc = subprocess.Popen([py, str(Path(__file__).resolve()), "_supervise", "--job", str(job)],
                                    creationflags=base | 0x01000000, **kwargs)  # CREATE_BREAKAWAY_FROM_JOB
        except OSError:
            proc = subprocess.Popen([py, str(Path(__file__).resolve()), "_supervise", "--job", str(job)],
                                    creationflags=base, **kwargs)
    else:
        proc = subprocess.Popen([py, str(Path(__file__).resolve()), "_supervise", "--job", str(job)],
                                start_new_session=True, **kwargs)
    for _ in range(40):  # 最多等 10 秒，确认真的起来了
        st = load_json(status_path, {})
        if st.get("state") in ("running", "done", "failed"):
            break
        time.sleep(0.25)
    st = load_json(status_path, {})
    print(f"已启动 {agent['name']} / {args.model}：任务 {job.name}，状态 {st.get('state')}，"
          f"进程 {st.get('pid')}（看护进程 {proc.pid}）。查进度：dispatch.py status --job \"{job}\"")
    return 0 if st.get("state") in ("running", "done") else 1


def supervise(job: Path, profile: dict) -> int:
    meta = load_json(job / "dispatch.json", {})
    status_path = job / "status.json"
    started = time.time()
    with open(job / "运行时间.txt", "a", encoding="utf-8") as fh:
        fh.write(f"开始 {now()}\n")
    stdin = open(job / "启动提示.md", "rb") if meta.get("prompt_via") == "stdin" else subprocess.DEVNULL
    out = open(job / meta.get("log", "运行日志.txt"), "ab")
    err = out if meta.get("stderr_to_log") else open(job / "运行错误.txt", "ab")
    kwargs = dict(stdin=stdin, stdout=out, stderr=err, cwd=meta.get("cwd") or str(job))
    env = os.environ.copy()
    env.update(profile.get("agents", {}).get(meta.get("agent"), {}).get("env", {}))
    kwargs["env"] = env
    if IS_WIN:
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(meta["cmd"], **kwargs)
    except OSError as exc:
        write_json(status_path, {"state": "failed", "error": f"启动失败：{exc}", "ended": now()})
        return 1
    write_json(status_path, {"state": "running", "pid": proc.pid, "supervisor_pid": os.getpid(), "started": now()})
    code = proc.wait()
    for fh in {out, err}:
        fh.close()
    if stdin is not subprocess.DEVNULL:
        stdin.close()
    minutes = round((time.time() - started) / 60, 1)
    current = load_json(status_path, {})
    state = "stopped" if current.get("state") == "stopping" else ("done" if code == 0 else "failed")
    agent = find_agent(load_catalog(), meta["agent"])
    hint = classify(job, agent, meta, failed=(code != 0))
    write_json(status_path, {"state": state, "pid": proc.pid, "exit_code": code, "started": current.get("started"),
                             "ended": now(), "minutes": minutes, "hint": hint})
    with open(job / "运行时间.txt", "a", encoding="utf-8") as fh:
        fh.write(f"结束 {now()}（退出码 {code}，{minutes} 分钟）\n")
    append_history(profile, {"time": now(), "event": "finished", "job": job.name, "agent": meta["agent"],
                             "model": meta["model"], "effort": meta.get("effort", ""),
                             "task_type": meta.get("task_type", ""), "state": state, "exit_code": code,
                             "minutes": minutes, "note": hint})
    return 0 if code == 0 else 1


def collect_jobs(args) -> list[Path]:
    jobs = [Path(expand(j)).resolve() for j in (args.job or [])]
    if args.root:
        root = Path(expand(args.root))
        jobs += sorted(p.parent for p in root.glob("*/dispatch.json"))
    if not jobs:
        raise SystemExit("用 --job 或 --root 指定任务。")
    return jobs


def job_row(job: Path) -> dict:
    meta = load_json(job / "dispatch.json", {})
    st = load_json(job / "status.json", {})
    state = st.get("state", "未启动")
    if state in ("running", "starting", "stopping") and not pid_alive(st.get("pid")) \
            and not pid_alive(st.get("supervisor_pid")):
        state = "中断（进程已不在）"
    log = job / meta.get("log", "运行日志.txt")
    size = log.stat().st_size if log.exists() else 0
    age = int(time.time() - log.stat().st_mtime) if log.exists() else None
    elapsed = st.get("minutes")
    if elapsed is None and st.get("started"):
        try:
            elapsed = round((dt.datetime.now() - dt.datetime.fromisoformat(st["started"])).total_seconds() / 60, 1)
        except ValueError:
            pass
    hint = st.get("hint", "")
    if state not in ("done",) and not hint and meta:
        try:
            hint = classify(job, find_agent(load_catalog(), meta["agent"]), meta)
        except SystemExit:
            pass
    return {"job": job.name, "agent": meta.get("agent", "?"), "model": meta.get("model", "?"), "state": state,
            "exit_code": st.get("exit_code", ""), "minutes": elapsed, "log_kb": round(size / 1024, 1),
            "log_idle_s": age, "hint": hint}


def print_rows(rows: list[dict]) -> None:
    print("| 任务 | 代理 | 模型 | 状态 | 退出码 | 用时（分钟） | 日志（KB） | 日志多久没动（秒） | 提示 |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['job']} | {r['agent']} | {r['model']} | {r['state']} | {r['exit_code']} | {r['minutes'] if r['minutes'] is not None else '—'} | "
              f"{r['log_kb']} | {r['log_idle_s'] if r['log_idle_s'] is not None else '—'} | {r['hint']} |")


def cmd_status(args, profile: dict) -> int:
    print_rows([job_row(j) for j in collect_jobs(args)])
    return 0


def cmd_wait(args, profile: dict) -> int:
    jobs = collect_jobs(args)
    deadline = time.time() + args.timeout
    while True:
        rows = [job_row(j) for j in jobs]
        if not any(r["state"] in ("running", "starting", "stopping") for r in rows):
            print_rows(rows)
            return 0
        if time.time() >= deadline:
            print(f"等了 {args.timeout} 秒，还有任务在跑：")
            print_rows(rows)
            return 2
        time.sleep(args.interval)


def cmd_stop(args, profile: dict) -> int:
    for job in collect_jobs(args):
        st = load_json(job / "status.json", {})
        if st.get("state") not in ("running", "starting"):
            print(f"{job.name}：状态是 {st.get('state')}，不用停。")
            continue
        st["state"] = "stopping"
        write_json(job / "status.json", st)
        for key in ("pid", "supervisor_pid"):
            pid = st.get(key)
            if pid and pid_alive(pid):
                if IS_WIN:
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    try:
                        os.killpg(int(pid), 9)
                    except OSError:
                        pass
        time.sleep(1)
        st = load_json(job / "status.json", {})
        if st.get("state") == "stopping":  # 看护进程也被杀了，自己补记
            st.update({"state": "stopped", "ended": now()})
            write_json(job / "status.json", st)
        print(f"{job.name}：已停止。剩下的半成品请挪开或删掉，免得混淆。")
    return 0


def cmd_review(args, profile: dict) -> int:
    job = Path(expand(args.job)).resolve()
    meta = load_json(job / "dispatch.json", {})
    append_history(profile, {"time": now(), "event": "review", "job": job.name, "agent": meta.get("agent", ""),
                             "model": meta.get("model", ""), "effort": meta.get("effort", ""),
                             "task_type": meta.get("task_type", ""), "accepted": args.accepted,
                             "note": args.note or ""})
    print(f"已记下：{job.name} 验收{'通过' if args.accepted == 'yes' else '不通过'}。")
    return 0


def main() -> int:
    utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("new-job")
    p.add_argument("--name", required=True)
    p.add_argument("--root")

    p = sub.add_parser("run")
    p.add_argument("--job", required=True)
    p.add_argument("--agent", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--effort")
    p.add_argument("--access", choices=["job", "workspace", "job_shell"])
    p.add_argument("--workdir")
    p.add_argument("--add-dir", action="append")
    p.add_argument("--offline", action="store_true")
    p.add_argument("--extra", action="append", help="原样追加的参数，可重复")
    p.add_argument("--task-type")
    p.add_argument("--foreground", action="store_true", help="在当前进程里看护到结束（宿主有后台通知时用）")
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("_supervise")
    p.add_argument("--job", required=True)

    for name in ("status", "wait", "stop"):
        p = sub.add_parser(name)
        p.add_argument("--job", action="append")
        p.add_argument("--root")
        if name == "wait":
            p.add_argument("--timeout", type=int, default=3000)
            p.add_argument("--interval", type=int, default=30)

    p = sub.add_parser("review")
    p.add_argument("--job", required=True)
    p.add_argument("--accepted", choices=["yes", "no"], required=True)
    p.add_argument("--note")

    args = ap.parse_args()
    profile = load_profile()
    if args.cmd == "_supervise":
        return supervise(Path(args.job), profile)
    return {"new-job": cmd_new_job, "run": cmd_run, "status": cmd_status, "wait": cmd_wait,
            "stop": cmd_stop, "review": cmd_review}[args.cmd](args, profile)


if __name__ == "__main__":
    sys.exit(main())
