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

    assert ("mk", "7-a", "main") in events  # persistent -> shared per-ticket path, title in slug
    assert ("run", "builder", "i1") in events
    assert ("rm", "7-a") in events
    assert own.owns("i1") is False
    # On success: a state change isn't forced and nothing is blocked (an exec report IS posted).
    assert fake_plane.states == []
    assert not any("blocked" in b.lower() for _, b in fake_plane.comments)
    assert any("builder run" in b for _, b in fake_plane.comments)  # per-stage exec report


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
    blocked = [(i, b) for i, b in fake_plane.comments if "blocked" in b.lower()]
    assert len(blocked) == 1
    issue_id, body = blocked[0]
    assert issue_id == "i1"
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
    assert any("continuing after reaching the turn limit" in b.lower()
               for _, b in fake_plane.comments)
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


def _mark(cfg, ticket_id, sid="sid-existing"):
    d = cfg.worktrees_root / ".sessions"
    d.mkdir(parents=True, exist_ok=True)
    (d / ticket_id).write_text(sid)


def test_exec_report_formats_metrics():
    from orchestrator.dispatch import _exec_report
    r = SessionResult(ok=True, model="claude-sonnet-4-6", num_turns=66,
                      duration_seconds=352.0, initial_input_tokens=28893,
                      total_input_tokens=2334266, output_tokens=13267, cost_usd=1.201)
    rpt = _exec_report("qa", r, resume=True)
    assert "**[orchestrator] qa run** — ok" in rpt
    for piece in ("claude-sonnet-4-6", "66 turns", "352s", "ctx ~28k",
                  "2334266/13267", "$1.20", "resumed"):
        assert piece in rpt
    bad = _exec_report("builder", SessionResult(ok=False, error="error_during_execution: boom"), False)
    assert "failed (error_during_execution: boom)" in bad


def test_exec_report_posted_per_stage(tmp_path):
    cfg = make_cfg(tmp_path)
    own = Ownership(); own.claim("i1")
    fake_plane = FakePlane()

    def fake_run(cfg, role, ticket_id, worktree, **k):
        return SessionResult(ok=True, model="m", num_turns=3, duration_seconds=12.0,
                             session_id="s")

    dispatch = make_dispatch(cfg, own, run=fake_run, mk_worktree=_mk,
                             rm_worktree=lambda r, w: None, plane=fake_plane)
    dispatch(Issue("i1", "a", "", "s-ready", 7), "builder")
    assert any(b.startswith("**[orchestrator] builder run**") for _, b in fake_plane.comments)


def test_builder_create_stores_assigned_session_id(tmp_path):
    cfg = make_cfg(tmp_path)
    own = Ownership(); own.claim("i1")
    seen = {}

    def fake_run(cfg, role, ticket_id, worktree, **k):
        seen.update(k)
        return SessionResult(ok=True, session_id="sid-new")

    dispatch = make_dispatch(cfg, own, run=fake_run, mk_worktree=_mk,
                             rm_worktree=lambda r, w: None, plane=FakePlane())
    dispatch(Issue("i1", "a", "", "s-ready", 7), "builder")
    assert seen["resume"] is False                                      # first run -> create
    assert seen["session_id"] == ""                                     # nothing forced on create
    marker = cfg.worktrees_root / ".sessions" / "i1"
    assert marker.read_text() == "sid-new"                             # captured id stored for resume


def test_broken_resume_clears_marker_for_clean_restart(tmp_path):
    cfg = make_cfg(tmp_path)
    own = Ownership(); own.claim("i1")
    _mark(cfg, "i1", "sid-old")

    def fake_run(cfg, role, ticket_id, worktree, **k):
        return SessionResult(ok=False, error="session ended with no result event", session_id=None)

    dispatch = make_dispatch(cfg, own, run=fake_run, mk_worktree=_mk,
                             rm_worktree=lambda r, w: None, plane=FakePlane())
    dispatch(Issue("i1", "a", "", "s-inprog", 7), "builder")
    assert not (cfg.worktrees_root / ".sessions" / "i1").exists()       # marker dropped -> next is fresh


def test_builder_rework_resumes_persistent_session_with_feedback(tmp_path):
    cfg = make_cfg(tmp_path)
    own = Ownership(); own.claim("i1")
    _mark(cfg, "i1")  # session already exists
    fake_plane = FakePlane(comments=["**[reviewer] Review → In Progress** — fix the thing"])
    seen = {}

    def fake_run(cfg, role, ticket_id, worktree, **k):
        seen.update(k)
        return SessionResult(ok=True)

    dispatch = make_dispatch(cfg, own, run=fake_run, mk_worktree=_mk,
                             rm_worktree=lambda r, w: None, plane=fake_plane)
    dispatch(Issue("i1", "a", "", "s-inprog", 7), "builder")
    assert seen["resume"] is True
    assert seen["context"] == ""                         # not re-injected on resume
    assert "fix the thing" in seen["instruction"]        # latest feedback carried into the session


def test_branch_slug_is_relevant_and_stable():
    from orchestrator.dispatch import _branch_slug, _slugify
    assert _slugify("P1-T09 — Web: project detail") == "p1-t09-web-project-detail"
    assert _slugify("") == ""
    # over-limit titles trim at a word boundary
    assert _slugify("a b c d e f g h i j k l m n o p", limit=10) in ("a-b-c-d-e", "a-b-c-d-e-f")
    iss = Issue("u", "P1-T01 — Auth: register + login (bcrypt, JWT)", "", "s", 12)
    b = _branch_slug(iss, "builder", True)
    q = _branch_slug(iss, "qa", True)
    r = _branch_slug(iss, "reviewer", False)
    assert b == q                          # builder + QA share one stable per-ticket slug
    assert b.startswith("12-") and "auth" in b
    assert r == b + "-review"              # reviewer worktree is distinct
    # empty title -> falls back to the sequence id, never blank
    assert _branch_slug(Issue("u", "", "", "s", 5), "builder", True) == "5"


def test_persistent_roles_share_one_worktree_path(tmp_path):
    # builder + QA of the same ticket must use the SAME worktree path so a resume runs in the
    # cwd the session was created in; reviewer gets its own.
    cfg = make_cfg(tmp_path)
    own = Ownership()

    for role, expect in (("builder", "9-a"), ("qa", "9-a"), ("reviewer", "9-a-review")):
        own.claim("i9")
        seen = {}

        def mk(repo_dir, roots, slug, base_branch="main"):
            seen["slug"] = slug
            return roots / slug

        d = make_dispatch(cfg, own, run=lambda c, r, t, w, **k: SessionResult(ok=True, session_id="s"),
                          mk_worktree=mk, rm_worktree=lambda r, w: None,
                          verify=lambda c: (True, "ok"), plane=FakePlane())
        d(Issue("i9", "a", "", "s-ready", 9), role)
        assert seen["slug"] == expect


def test_error_during_execution_on_resume_heals_and_retries(tmp_path):
    cfg = make_cfg(tmp_path, max_turn_retries=2)
    own = Ownership(); own.claim("i1")
    _mark(cfg, "i1", "sid-old")
    fake_plane = FakePlane()

    def fake_run(cfg, role, ticket_id, worktree, **k):
        return SessionResult(ok=False, error="error_during_execution: tool blew up", session_id="sid-old")

    dispatch = make_dispatch(cfg, own, run=fake_run, mk_worktree=_mk,
                             rm_worktree=lambda r, w: None, plane=fake_plane)
    dispatch(Issue("i1", "a", "", "s-qa", 7), "qa")
    assert not (cfg.worktrees_root / ".sessions" / "i1").exists()  # broken resume healed -> fresh next
    assert fake_plane.states == []                                 # NOT blocked
    assert any("retrying after a recoverable session issue" in b.lower()
               for _, b in fake_plane.comments)
    assert own.owns("i1") is False


def test_session_timeout_on_create_is_transient_and_stores_session_for_resume(tmp_path):
    cfg = make_cfg(tmp_path, max_turn_retries=2)
    own = Ownership(); own.claim("i1")
    fake_plane = FakePlane()

    def fake_run(cfg, role, ticket_id, worktree, **k):
        # timed-out session: captured its id from the partial stream, no ok result
        return SessionResult(ok=False, error="session timeout", session_id="sid-partial")

    dispatch = make_dispatch(cfg, own, run=fake_run, mk_worktree=_mk,
                             rm_worktree=lambda r, w: None, plane=fake_plane)
    dispatch(Issue("i1", "a", "", "s-ready", 7), "builder")
    assert fake_plane.states == []                                       # NOT blocked (transient)
    assert (cfg.worktrees_root / ".sessions" / "i1").read_text() == "sid-partial"  # resumable next
    assert any("retrying after a recoverable session issue" in b.lower()
               for _, b in fake_plane.comments)


def test_error_during_execution_blocks_after_retries_exhausted(tmp_path):
    cfg = make_cfg(tmp_path, max_turn_retries=1)
    own = Ownership(); own.claim("i1")
    _mark(cfg, "i1", "sid")
    fake_plane = FakePlane(comments=[
        "**[orchestrator] retrying after a recoverable session issue** — earlier attempt"])

    def fake_run(cfg, role, ticket_id, worktree, **k):
        return SessionResult(ok=False, error="error_during_execution: boom", session_id="sid")

    dispatch = make_dispatch(cfg, own, run=fake_run, mk_worktree=_mk,
                             rm_worktree=lambda r, w: None, plane=fake_plane)
    dispatch(Issue("i1", "a", "", "s-qa", 7), "qa")
    assert fake_plane.states == [("i1", "s-blocked")]             # retry budget spent -> blocked


def test_qa_resumes_same_session_with_merge_instruction(tmp_path):
    cfg = make_cfg(tmp_path)
    own = Ownership(); own.claim("i1")
    _mark(cfg, "i1")
    seen = {}

    def fake_run(cfg, role, ticket_id, worktree, **k):
        seen.update(k)
        return SessionResult(ok=True)

    dispatch = make_dispatch(cfg, own, run=fake_run, mk_worktree=_mk,
                             rm_worktree=lambda r, w: None, verify=lambda c: (True, "ok"),
                             plane=FakePlane())
    dispatch(Issue("i1", "a", "", "s-qa", 7), "qa")
    assert seen["resume"] is True and "merge" in seen["instruction"].lower()


def test_reviewer_is_fresh_even_when_session_exists(tmp_path):
    cfg = make_cfg(tmp_path)
    own = Ownership(); own.claim("i1")
    _mark(cfg, "i1")  # a builder session exists, but reviewer must NOT resume it
    seen = {}

    def fake_run(cfg, role, ticket_id, worktree, **k):
        seen.update(k)
        return SessionResult(ok=True)

    dispatch = make_dispatch(cfg, own, run=fake_run, mk_worktree=_mk,
                             rm_worktree=lambda r, w: None, plane=FakePlane())
    dispatch(Issue("i1", "a", "", "s-review", 7), "reviewer")
    assert seen["resume"] is False and seen["context"] != ""  # independent + gets its own context


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
    assert any("main is RED" in b for _, b in fake_plane.comments)


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

    assert not any("main is RED" in b for _, b in fake_plane.comments)  # silent about health
    assert own.owns("i1") is False
