from pathlib import Path
from orchestrator.dispatch import make_dispatch
from orchestrator.poller import Ownership
from orchestrator.launcher import SessionResult
from orchestrator.plane import Issue
from orchestrator.config import Config


def make_cfg(tmp_path):
    return Config(
        plane_base_url="x", plane_api_key="k", plane_workspace_slug="w", plane_project_id="p",
        github_repo="o/r", repo_dir=tmp_path / "repo", worktrees_root=tmp_path / "wt",
        poll_interval_seconds=1, claude_binary="claude", claude_model="m",
        mcp_config_path=tmp_path / "mcp.json", templates_dir=tmp_path / "t",
        state_ids={}, max_concurrency=1,
    )


def test_dispatch_runs_session_then_cleans_up_and_releases(tmp_path):
    cfg = make_cfg(tmp_path)
    own = Ownership()
    own.claim("i1")
    events = []

    def fake_mk(repo_dir, roots, slug):
        events.append(("mk", slug))
        return roots / slug

    def fake_rm(repo_dir, wt):
        events.append(("rm", wt.name))

    def fake_run(cfg, role, ticket_id, worktree):
        events.append(("run", role, ticket_id))
        return SessionResult(ok=True)

    dispatch = make_dispatch(cfg, own, run=fake_run, mk_worktree=fake_mk, rm_worktree=fake_rm)
    dispatch(Issue("i1", "a", "", "s-ready", 7), "builder")

    assert ("mk", "7-builder") in events
    assert ("run", "builder", "i1") in events
    assert ("rm", "7-builder") in events
    assert own.owns("i1") is False


def test_dispatch_releases_ownership_even_on_session_error(tmp_path):
    cfg = make_cfg(tmp_path)
    own = Ownership()
    own.claim("i1")

    def fake_run(cfg, role, ticket_id, worktree):
        return SessionResult(ok=False, error="boom")

    dispatch = make_dispatch(cfg, own, run=fake_run,
                             mk_worktree=lambda r, roots, s: roots / s,
                             rm_worktree=lambda r, w: None)
    dispatch(Issue("i1", "a", "", "s-ready", 7), "builder")
    assert own.owns("i1") is False
