from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import yaml

REQUIRED = [
    "plane_base_url", "plane_api_key", "plane_workspace_slug", "plane_project_id",
    "github_repo", "repo_dir", "worktrees_root", "poll_interval_seconds",
    "claude_binary", "claude_model", "mcp_config_path", "templates_dir", "state_ids",
]


@dataclass
class Config:
    plane_base_url: str
    plane_api_key: str
    plane_workspace_slug: str
    plane_project_id: str
    github_repo: str
    repo_dir: Path
    worktrees_root: Path
    poll_interval_seconds: int
    claude_binary: str
    claude_model: str
    mcp_config_path: Path
    templates_dir: Path
    state_ids: dict[str, str]
    max_concurrency: int = 1
    session_timeout_seconds: int = 1800
    max_turns: int = 80
    # how many times a ticket may auto-continue after a session hits max_turns
    # (progress is usually made) before it is parked in Blocked for a human
    max_turn_retries: int = 1
    # per-role model overrides (e.g. {"reviewer": "claude-opus-4-8"}); falls back to claude_model
    role_models: dict = field(default_factory=dict)
    base_branch: str = "main"
    # Shell command that must pass for trunk to be considered healthy after a merge.
    # When None, the post-merge main-health check is skipped.
    verify_cmd: str | None = None
    # How many reviewer/QA → In Progress bounces a ticket may take before it is
    # parked in Blocked for a human instead of looping forever.
    max_reworks: int = 3


def load_config(path: Path) -> Config:
    data = yaml.safe_load(Path(path).read_text()) or {}
    for key in REQUIRED:
        if key not in data:
            raise KeyError(f"missing required config key: {key}")
    return Config(
        plane_base_url=data["plane_base_url"].rstrip("/"),
        plane_api_key=data["plane_api_key"],
        plane_workspace_slug=data["plane_workspace_slug"],
        plane_project_id=data["plane_project_id"],
        github_repo=data["github_repo"],
        repo_dir=Path(data["repo_dir"]),
        worktrees_root=Path(data["worktrees_root"]),
        poll_interval_seconds=int(data["poll_interval_seconds"]),
        claude_binary=data["claude_binary"],
        claude_model=data["claude_model"],
        mcp_config_path=Path(data["mcp_config_path"]),
        templates_dir=Path(data["templates_dir"]),
        state_ids=dict(data["state_ids"]),
        max_concurrency=int(data.get("max_concurrency", 1)),
        session_timeout_seconds=int(data.get("session_timeout_seconds", 1800)),
        max_turns=int(data.get("max_turns", 80)),
        max_turn_retries=int(data.get("max_turn_retries", 1)),
        role_models=dict(data.get("role_models", {})),
        base_branch=data.get("base_branch", "main"),
        verify_cmd=data.get("verify_cmd"),
        max_reworks=int(data.get("max_reworks", 3)),
    )
