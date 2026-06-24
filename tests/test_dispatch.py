from dataclasses import dataclass
from pathlib import Path
from orchestrator.dispatch import make_dispatch
from orchestrator.poller import Ownership
from orchestrator.launcher import SessionResult
from orchestrator.plane import Issue
from orchestrator.config import Config


def make_cfg(tmp_path, **over):
    base = dict(
        plane_base_url="x", plane_api_key="k", plane_workspace_slug="w", plane_project_id="p",
        github_repo="o/r", repo_dir=tmp_path / "repo", worktrees_root=tmp_path / "wt",
        poll_interval_seconds=1, claude_binary="claude", claude_model="m",
        mcp_config_path=tmp_path / "mcp.json", templates_dir=tmp_path / "t",
        state_ids={"Blocked": "s-blocked"}, max_concurrency=1,
    )
    base.update(over)
    return Config(**base)


@dataclass
class FakeComment:
    body_html: str


class FakePlane:
    def __init__(self, comments=None):
        self.comments: list[tuple[str, str]] = []
        self.states: list[tuple[str, str]] = []
        self._preset = comments or []

    def add_comment(self, issue_id: str, body: str) -> None:
        self.comments.append((issue_id, body))

    def set_state(self, issue_id: str, state_id: str) -> None:
        self.states.append((issue_id, state_id))

    def list_comments(self, issue_id: str):
        return [FakeComment(b) for b in self._preset]


def _mk(repo_dir, roots, slug, base_branch="main"):
    return roots / slug


def test_dispatch_runs_session_then_cleans_up_and_releases(tmp_path):
    cfg = make_cfg(tmp_path)
    own = Ownership()
    own.claim("i1")
    events = []
    fake_plane = FakePlane()

    def fake_mk(repo_dir, roots, slug, base_branch="main"):
        events.append(("mk", slug, base_branch))
        return roots / slug

    def fake_rm(repo_dir, wt):
        events.append(("rm", wt.name))

    def fake_run(cfg, role, ticket_id, worktree, **k):
        events.append(("run", role, ticket_id))
        return SessionResult(ok=True)

    dispatch = make_dispatch(cfg, own, run=fake_run, mk_worktree=fake_mk, rm_worktree=fake_rm,
                             plane=fake_plane)
    dispatch(Issue("i1", "a", "", "s-ready", 7), "builder")

    assert ("mk", "7-builder", "main") in events  # worktree cut from the configured base branch
    assert ("run", "builder", "i1") in events
    assert ("rm", "7-builder") in events
    assert own.owns("i1") is False
    # On success, no Blocked comment or state change
    assert fake_plane.comments == []
    assert fake_plane.states == []


def test_dispatch_releases_ownership_and_blocks_on_session_error(tmp_path):
    cfg = make_cfg(tmp_path)
    own = Ownership()
    own.claim("i1")
    fake_plane = FakePlane()

    def fake_run(cfg, role, ticket_id, worktree, **k):
        return SessionResult(ok=False, error="boom")

    dispatch = make_dispatch(cfg, own, run=fake_run, mk_worktree=_mk,
                             rm_worktree=lambda r, w: None, plane=fake_plane)
    dispatch(Issue("i1", "a", "", "s-ready", 7), "builder")

    assert own.owns("i1") is False
    assert len(fake_plane.comments) == 1
    issue_id, body = fake_plane.comments[0]
    assert issue_id == "i1"
    assert "blocked" in body.lower()
    assert "🤖" not in body and "\U0001f916" not in body  # no emojis in posted comments
    assert fake_plane.states == [("i1", "s-blocked")]


def test_dispatch_releases_and_blocks_on_exception(tmp_path):
    cfg = make_cfg(tmp_path)
    own = Ownership()
    own.claim("i1")
    fake_plane = FakePlane()

    def fake_run(cfg, role, ticket_id, worktree, **k):
        raise RuntimeError("unexpected crash")

    dispatch = make_dispatch(cfg, own, run=fake_run, mk_worktree=_mk,
                             rm_worktree=lambda r, w: None, plane=fake_plane)
    dispatch(Issue("i1", "a", "", "s-ready", 7), "builder")

    assert own.owns("i1") is False
    assert len(fake_plane.comments) == 1
    assert "blocked" in fake_plane.comments[0][1].lower()
    assert fake_plane.states == [("i1", "s-blocked")]


def test_rework_cap_blocks_thrashing_ticket_without_running(tmp_path):
    cfg = make_cfg(tmp_path, max_reworks=3)
    own = Ownership()
    own.claim("i1")
    # three reviewer/QA bounces already on the trail → at the cap
    fake_plane = FakePlane(comments=[
        "**[reviewer] Review → In Progress** — changes requested",
        "**[qa] QA → In Progress** — QA failed",
        "**[reviewer] Review → In Progress** — changes requested again",
    ])
    ran = []

    def fake_run(cfg, role, ticket_id, worktree, **k):
        ran.append(ticket_id)
        return SessionResult(ok=True)

    dispatch = make_dispatch(cfg, own, run=fake_run, mk_worktree=_mk,
                             rm_worktree=lambda r, w: None, plane=fake_plane)
    dispatch(Issue("i1", "a", "", "s-inprog", 7), "builder")

    assert ran == []                       # no session launched
    assert fake_plane.states == [("i1", "s-blocked")]
    assert "rework" in fake_plane.comments[0][1].lower()
    assert own.owns("i1") is False


def test_max_turns_requeues_instead_of_blocking(tmp_path):
    cfg = make_cfg(tmp_path, max_turn_retries=1)
    own = Ownership(); own.claim("i1")
    fake_plane = FakePlane()  # no prior continuations on the trail

    dispatch = make_dispatch(
        cfg, own, run=lambda c, r, t, w, **k: SessionResult(ok=False, error="error_max_turns"),
        mk_worktree=_mk, rm_worktree=lambda r, w: None, plane=fake_plane)
    dispatch(Issue("i1", "a", "", "s-inprog", 7), "builder")

    assert fake_plane.states == []                       # NOT blocked
    assert "continuing after reaching the turn limit" in fake_plane.comments[0][1].lower()
    assert own.owns("i1") is False                       # released -> next poll re-picks it up


def test_max_turns_blocks_after_retries_exhausted(tmp_path):
    cfg = make_cfg(tmp_path, max_turn_retries=1)
    own = Ownership(); own.claim("i1")
    # one continuation already happened -> at the limit -> must block now
    fake_plane = FakePlane(comments=[
        "**[orchestrator] continuing after reaching the turn limit** — earlier attempt"])

    dispatch = make_dispatch(
        cfg, own, run=lambda c, r, t, w, **k: SessionResult(ok=False, error="error_max_turns"),
        mk_worktree=_mk, rm_worktree=lambda r, w: None, plane=fake_plane)
    dispatch(Issue("i1", "a", "", "s-inprog", 7), "builder")

    assert fake_plane.states == [("i1", "s-blocked")]    # blocked this time
    assert own.owns("i1") is False


def test_ticket_context_built_from_held_data(tmp_path):
    from orchestrator.dispatch import ticket_context
    cfg = make_cfg(tmp_path, state_ids={"In Progress": "sip", "Review": "srev", "Blocked": "s-blocked"})
    issue = Issue("u1", "My Task", "<p>AC: do X</p>", "sip", 7)
    ctx = ticket_context(cfg, issue, [FakeComment("<p>note one</p>")])
    assert "My Task" in ctx and "In Progress" in ctx and "AC: do X" in ctx
    assert "note one" in ctx and "sip" in ctx          # comment + state-id map present
    assert "do not re-fetch" in ctx.lower() and "only to write" in ctx.lower()


def test_usage_limit_pauses_daemon_not_blocks(tmp_path):
    from orchestrator.poller import usage_limit_hit
    usage_limit_hit.clear()
    cfg = make_cfg(tmp_path)
    own = Ownership(); own.claim("i1")
    fake_plane = FakePlane()

    dispatch = make_dispatch(
        cfg, own, run=lambda c, r, t, w, **k: SessionResult(ok=False, error="usage_limit"),
        mk_worktree=_mk, rm_worktree=lambda r, w: None, plane=fake_plane)
    dispatch(Issue("i1", "a", "", "s-inprog", 7), "builder")

    assert fake_plane.states == []          # NOT blocked
    assert usage_limit_hit.is_set()         # daemon cooldown tripped
    assert own.owns("i1") is False
    usage_limit_hit.clear()


def test_qa_success_runs_main_health_and_alerts_when_red(tmp_path):
    cfg = make_cfg(tmp_path)
    own = Ownership()
    own.claim("i1")
    fake_plane = FakePlane()

    dispatch = make_dispatch(
        cfg, own,
        run=lambda c, role, tid, wt, **k: SessionResult(ok=True),
        mk_worktree=_mk, rm_worktree=lambda r, w: None,
        verify=lambda c: (False, "1 test failed"),
        plane=fake_plane)
    dispatch(Issue("i1", "a", "", "s-qa", 7), "qa")

    assert own.owns("i1") is False
    assert fake_plane.states == []                       # Completed already; not re-moved
    assert len(fake_plane.comments) == 1
    assert "main is RED" in fake_plane.comments[0][1]


def test_qa_success_silent_when_main_green(tmp_path):
    cfg = make_cfg(tmp_path)
    own = Ownership()
    own.claim("i1")
    fake_plane = FakePlane()

    dispatch = make_dispatch(
        cfg, own,
        run=lambda c, role, tid, wt, **k: SessionResult(ok=True),
        mk_worktree=_mk, rm_worktree=lambda r, w: None,
        verify=lambda c: (True, "ok"),
        plane=fake_plane)
    dispatch(Issue("i1", "a", "", "s-qa", 7), "qa")

    assert fake_plane.comments == []
    assert own.owns("i1") is False
