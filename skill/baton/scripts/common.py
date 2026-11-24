"""scan_agents.py 和 dispatch.py 共用的小工具：路径、配置、带超时的子进程。

只用标准库（Python 3.9+）。不读取任何凭证文件。
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
SKILL_NAME = SKILL_DIR.name
IS_WIN = sys.platform == "win32"

# 绝不读取的文件名（凭证、令牌、环境变量文件）。扫描和派活都不碰。
SECRET_NAMES = {"auth.json", ".env", "credentials.json", ".credentials.json", "oauth_creds.json", "config.toml"}


def utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def expand(path: str) -> str:
    """展开 ~ 和 %VAR% / $VAR，统一斜杠。"""
    return os.path.normpath(os.path.expandvars(os.path.expanduser(path)))


def load_json(path: Path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except Exception as exc:  # 坏文件不让脚本整体失败
        print(f"[警告] 读不了 {path}：{exc}", file=sys.stderr)
        return default


def load_catalog() -> dict:
    return load_json(SCRIPT_DIR / "agent_catalog.json", {"agents": []})


def load_profile() -> dict:
    """个人配置：<skill>/local/profile.json。通用版没有这个文件，返回空配置。"""
    return load_json(SKILL_DIR / "local" / "profile.json", {})


def state_dir(profile: dict) -> Path:
    root = os.environ.get("BATON_STATE_DIR") or profile.get("state_dir") or "~/.baton"
    path = Path(expand(root))
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        # 沙箱里常见：状态目录不可写。退到临时目录，照常工作，但提醒中控。
        fallback = Path(tempfile.gettempdir()) / "baton_state"
        fallback.mkdir(parents=True, exist_ok=True)
        print(f"[警告] 状态目录 {path} 不可写（{exc.__class__.__name__}），这次改用 {fallback}。"
              f"要读写正式的榜单缓存和战绩，请在沙箱外运行，或把 {path} 加进可写目录。", file=sys.stderr)
        return fallback
    return path


def kill_tree(proc: subprocess.Popen) -> None:
    """连子进程一起杀。Windows 上 .cmd 启动器会再拉起 node 和原生程序，只杀父进程会留下孤儿并卡住管道。"""
    try:
        if IS_WIN:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def run_capture(args: list[str], timeout: int = 20, cwd: str | None = None) -> tuple[int | None, str]:
    """带超时运行命令，输出写临时文件而不是管道（避免孙进程占住管道导致挂起）。

    返回 (退出码或 None 表示超时, 合并后的输出文本)。
    """
    with tempfile.TemporaryDirectory(prefix="baton_") as tmp:
        out_path = Path(tmp) / "out.txt"
        with open(out_path, "wb") as out:
            kwargs = dict(stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, cwd=cwd or tmp)
            if IS_WIN:
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            else:
                kwargs["start_new_session"] = True
            try:
                proc = subprocess.Popen(args, **kwargs)
            except OSError as exc:
                return -1, f"启动失败：{exc}"
            try:
                code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                kill_tree(proc)
                code = None
        text = out_path.read_bytes().decode("utf-8", errors="replace")
    return code, text


def find_executable(agent: dict, profile_agent: dict) -> str | None:
    """按顺序找：个人配置里的路径 → 环境变量提示 → PATH → 常见安装位置（取最新的一个）。"""
    if agent.get("kind") == "test":
        return sys.executable
    if profile_agent.get("path"):
        path = expand(profile_agent["path"])
        return path if os.path.exists(path) else None
    for var in agent.get("env_hints", []):
        value = os.environ.get(var)
        if value and os.path.exists(value) and Path(value).name.lower().startswith(agent["executables"][0]):
            return value
    for name in agent.get("executables", []):
        found = shutil.which(name)
        if found:
            return found
    plat = "win32" if IS_WIN else ("darwin" if sys.platform == "darwin" else "linux")
    hits: list[str] = []
    for pattern in agent.get("known_locations", {}).get(plat, []):
        hits.extend(p for p in glob.glob(expand(pattern)) if os.path.isfile(p))
    if hits:
        return max(hits, key=os.path.getmtime)
    return None


def detect_host() -> str | None:
    """猜当前中控是哪个代理。猜不出返回 None，由调用方用 --host 指定。"""
    if os.environ.get("CLAUDECODE") == "1":
        return "claude"
    if os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_SANDBOX"):
        return "codex"
    if any(k.startswith("GROK_") for k in os.environ):
        return "grok"
    if any(k.startswith(("ANTIGRAVITY", "AGY_")) for k in os.environ):
        return "agy"
    return None
