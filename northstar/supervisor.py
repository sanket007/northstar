from __future__ import annotations
from pathlib import Path
import shlex
import sys

from northstar.proc import run
from northstar import paths


def session_name(project: str) -> str:
    return f"ns-{project}"


def is_running(project: str, runner=run) -> bool:
    return runner(["tmux", "has-session", "-t", session_name(project)]).ok


def start(project: str, repo_dir: Path, plane_env: dict, runner=run) -> None:
    if is_running(project, runner=runner):
        return
    cfg = paths.project_config_path(project)
    envstr = " ".join(f"{k}={shlex.quote(str(v))}" for k, v in plane_env.items())
    inner = f"env {envstr} {shlex.quote(sys.executable)} -m orchestrator --config {shlex.quote(str(cfg))}"
    runner(f"tmux new-session -d -s {session_name(project)} -c {shlex.quote(str(repo_dir))} "
           f"{shlex.quote(inner)}", shell=True)
    log = paths.log_path(project)
    runner(f"tmux pipe-pane -t {session_name(project)} -o "
           f"{shlex.quote('cat >> ' + str(log))}", shell=True)


def stop(project: str, runner=run) -> None:
    runner(["tmux", "kill-session", "-t", session_name(project)])


def restart(project: str, repo_dir: Path, plane_env: dict, runner=run) -> None:
    stop(project, runner=runner)
    start(project, repo_dir, plane_env, runner=runner)


def status(project_names, runner=run) -> list[dict]:
    return [{"name": n, "running": is_running(n, runner=runner)} for n in project_names]


def logs_command(project: str, follow: bool) -> list[str]:
    if follow:
        return ["tmux", "attach", "-t", session_name(project)]
    return ["tail", "-n", "200", str(paths.log_path(project))]
