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
    # per-session turn ceiling. Keep this moderate: it bounds how much context one
    # session accumulates. Total work scales via max_turn_retries (each continuation is
    # a fresh process = a clean context window), not via a huge single-session budget.
    max_turns: int = 80
    # how many times a ticket may auto-continue after a session hits max_turns before it
    # is parked in Blocked for a human. Each continuation is a fresh session that resumes
    # from the branch's pushed commits — effectively a compaction (context resets to ~0),
    # so a high value buys lots of total work without ever bloating one session's context.
    max_turn_retries: int = 4
    # per-role model overrides (e.g. {"reviewer": "claude-opus-4-8"}); falls back to claude_model
    role_models: dict = field(default_factory=dict)
    base_branch: str = "main"
    # Shell command that must pass for trunk to be considered healthy after a merge.
    # When None, the post-merge main-health check is skipped.
    verify_cmd: str | None = None
    # How many reviewer/QA → In Progress bounces a ticket may take before it is
    # parked in Blocked for a human instead of looping forever.
    max_reworks: int = 3
    # How long the daemon pauses after a session hits the Claude plan's usage/session
    # limit, before it resumes polling (gives the plan window time to reset).
    usage_limit_cooldown_seconds: int = 900
    # Work-type labels (set by the importer) whose tickets skip the reviewer session: the
    # orchestrator auto-advances Review -> QA without launching a reviewer. For low-risk
    # work (docs, chores). QA is never skipped — it owns merge + trunk verify.
    skip_review_labels: list[str] = field(default_factory=list)


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
        max_turn_retries=int(data.get("max_turn_retries", 4)),
        role_models=dict(data.get("role_models", {})),
        base_branch=data.get("base_branch", "main"),
        verify_cmd=data.get("verify_cmd"),
        max_reworks=int(data.get("max_reworks", 3)),
        usage_limit_cooldown_seconds=int(data.get("usage_limit_cooldown_seconds", 900)),
        skip_review_labels=list(data.get("skip_review_labels", [])),
    )
