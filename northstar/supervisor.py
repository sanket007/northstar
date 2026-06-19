from __future__ import annotations
from pathlib import Path
import os
import shlex
import signal
import subprocess
import sys

from northstar.proc import run
from northstar import paths


# ---- tmux backend (existing behavior) ----
def session_name(project: str) -> str:
    return f"ns-{project}"


def _tmux_is_running(project: str, runner) -> bool:
    return runner(["tmux", "has-session", "-t", session_name(project)]).ok


def _tmux_start(project: str, repo_dir: Path, plane_env: dict, runner) -> None:
    if _tmux_is_running(project, runner):
        return
    cfg = paths.project_config_path(project)
    envstr = " ".join(f"{k}={shlex.quote(str(v))}" for k, v in plane_env.items())
    inner = (f"env {envstr} {shlex.quote(sys.executable)} -m orchestrator "
             f"--config {shlex.quote(str(cfg))}")
    runner(f"tmux new-session -d -s {session_name(project)} -c {shlex.quote(str(repo_dir))} "
           f"{shlex.quote(inner)}", shell=True)
    log = paths.log_path(project)
    runner(f"tmux pipe-pane -t {session_name(project)} -o {shlex.quote('cat >> ' + str(log))}",
           shell=True)


def _tmux_stop(project: str, runner) -> None:
    runner(["tmux", "kill-session", "-t", session_name(project)])


def _tmux_logs_command(project: str, follow: bool) -> list[str]:
    if follow:
        return ["tmux", "attach", "-t", session_name(project)]
    return ["tail", "-n", "200", str(paths.log_path(project))]


# ---- detached backend (no dependency) ----
def _pid_path(project: str) -> Path:
    return paths.home() / "run" / f"{project}.pid"


def _detached_is_running(project: str) -> bool:
    p = _pid_path(project)
    if not p.exists():
        return False
    try:
        pid = int(p.read_text().strip())
    except (ValueError, OSError):
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _detached_start(project: str, repo_dir: Path, plane_env: dict,
                    spawn=subprocess.Popen) -> None:
    if _detached_is_running(project):
        return
    (paths.home() / "run").mkdir(parents=True, exist_ok=True)
    cfg = paths.project_config_path(project)
    log = paths.log_path(project)
    log.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "orchestrator", "--config", str(cfg)]
    env = {**os.environ, **plane_env}
    logf = open(log, "a")
    try:
        proc = spawn(cmd, cwd=str(repo_dir), env=env, stdout=logf,
                     stderr=subprocess.STDOUT, start_new_session=True)
    finally:
        logf.close()  # the child keeps its own dup'd fd; the parent's copy is not needed
    _pid_path(project).write_text(str(proc.pid))


def _detached_stop(project: str) -> None:
    p = _pid_path(project)
    if not p.exists():
        return
    try:
        os.kill(int(p.read_text().strip()), signal.SIGTERM)
    except (OSError, ValueError):
        pass
    p.unlink(missing_ok=True)


def _detached_logs_command(project: str, follow: bool) -> list[str]:
    log = str(paths.log_path(project))
    return ["tail", "-f", log] if follow else ["tail", "-n", "200", log]


# ---- dispatch ----
def is_running(project: str, runner=run) -> bool:
    if paths.get_backend() == "detached":
        return _detached_is_running(project)
    return _tmux_is_running(project, runner)


def start(project: str, repo_dir: Path, plane_env: dict, runner=run) -> None:
    if paths.get_backend() == "detached":
        _detached_start(project, repo_dir, plane_env)
        return
    if not runner(["tmux", "-V"]).ok:
        raise RuntimeError("tmux backend configured but tmux not found — "
                           "run `northstar init --backend detached`")
    _tmux_start(project, repo_dir, plane_env, runner)


def stop(project: str, runner=run) -> None:
    if paths.get_backend() == "detached":
        _detached_stop(project)
        return
    _tmux_stop(project, runner)


def restart(project: str, repo_dir: Path, plane_env: dict, runner=run) -> None:
    stop(project, runner=runner)
    start(project, repo_dir, plane_env, runner=runner)


def status(project_names, runner=run) -> list[dict]:
    return [{"name": n, "running": is_running(n, runner=runner)} for n in project_names]


def logs_command(project: str, follow: bool) -> list[str]:
    if paths.get_backend() == "detached":
        return _detached_logs_command(project, follow)
    return _tmux_logs_command(project, follow)
