from orchestrator.poller import Ownership, poll_once
from orchestrator.plane import Issue
from orchestrator.config import Config
from pathlib import Path


def make_cfg(states, concurrency=1) -> Config:
    return Config(
        plane_base_url="x", plane_api_key="k", plane_workspace_slug="w", plane_project_id="p",
        github_repo="o/r", repo_dir=Path("/tmp/r"), worktrees_root=Path("/tmp/wt"),
        poll_interval_seconds=1, claude_binary="claude", claude_model="m",
        mcp_config_path=Path("/tmp/mcp.json"), templates_dir=Path("/tmp/t"),
        state_ids=states, max_concurrency=concurrency,
    )


class FakeClient:
    def __init__(self, by_state):
        self.by_state = by_state

    def list_issues_in_state(self, state_id):
        return self.by_state.get(state_id, [])

    def list_blocked_by(self, issue_id):
        return []


def test_poll_dispatches_actionable_with_correct_role():
    states = {"Ready to Dev": "s-ready", "Review": "s-review", "QA": "s-qa",
              "In Progress": "s-prog", "Blocked": "s-blk", "Completed": "s-done"}
    cfg = make_cfg(states, concurrency=5)
    client = FakeClient({
        "s-ready": [Issue("i1", "a", "", "s-ready", 1)],
        "s-review": [Issue("i2", "b", "", "s-review", 2)],
        "s-qa": [Issue("i3", "c", "", "s-qa", 3)],
    })
    own = Ownership()
    calls = []
    poll_once(client, cfg, own, lambda issue, role: calls.append((issue.id, role)))
    assert set(calls) == {("i1", "builder"), ("i2", "reviewer"), ("i3", "qa")}
    assert own.count() == 3


def test_poll_respects_concurrency_cap():
    states = {"Ready to Dev": "s-ready", "Review": "s-r", "QA": "s-q",
              "In Progress": "s-p", "Blocked": "s-b", "Completed": "s-d"}
    cfg = make_cfg(states, concurrency=1)
    client = FakeClient({"s-ready": [Issue("i1", "a", "", "s-ready", 1),
                                     Issue("i2", "b", "", "s-ready", 2)]})
    own = Ownership()
    calls = []
    poll_once(client, cfg, own, lambda issue, role: calls.append(issue.id))
    assert len(calls) == 1


def test_poll_skips_owned_tickets():
    states = {"Ready to Dev": "s-ready", "Review": "s-r", "QA": "s-q",
              "In Progress": "s-p", "Blocked": "s-b", "Completed": "s-d"}
    cfg = make_cfg(states, concurrency=5)
    client = FakeClient({"s-ready": [Issue("i1", "a", "", "s-ready", 1)]})
    own = Ownership()
    own.claim("i1")
    calls = []
    poll_once(client, cfg, own, lambda issue, role: calls.append(issue.id))
    assert calls == []


def test_run_survives_poll_exception(monkeypatch):
    from orchestrator import poller
    from orchestrator.config import Config
    from pathlib import Path
    cfg = Config(plane_base_url="x", plane_api_key="k", plane_workspace_slug="w", plane_project_id="p",
                 github_repo="o/r", repo_dir=Path("/t"), worktrees_root=Path("/t"), poll_interval_seconds=0,
                 claude_binary="claude", claude_model="m", mcp_config_path=Path("/t/m.json"),
                 templates_dir=Path("/t"), state_ids={}, max_concurrency=1)
    class BoomClient:
        def list_issues_in_state(self, s): raise RuntimeError("plane down")
    calls = {"n": 0}
    def fake_sleep(_): calls["n"] += 1
    # must NOT raise; loop runs max_iterations then returns
    poller.run(cfg, client=BoomClient(), dispatch=lambda i, r: None, sleep=fake_sleep, max_iterations=2)
    assert calls["n"] == 2


def test_poll_once_short_circuits_when_full():
    from orchestrator.poller import Ownership, poll_once
    from orchestrator.config import Config
    from pathlib import Path
    cfg = Config(plane_base_url="x", plane_api_key="k", plane_workspace_slug="w", plane_project_id="p",
                 github_repo="o/r", repo_dir=Path("/t"), worktrees_root=Path("/t"), poll_interval_seconds=0,
                 claude_binary="c", claude_model="m", mcp_config_path=Path("/t/m.json"), templates_dir=Path("/t"),
                 state_ids={"Ready to Dev": "s", "In Progress": "s2", "Review": "s3", "QA": "s4"}, max_concurrency=1)
    own = Ownership(); own.claim("already")
    listed = []
    class C:
        def list_issues_in_state(self, s): listed.append(s); return []
    poll_once(C(), cfg, own, lambda i, r: None)
    assert listed == []  # full -> no list calls at all


def _cfg_with_states():
    from orchestrator.config import Config
    from pathlib import Path
    return Config(plane_base_url="x", plane_api_key="k", plane_workspace_slug="w", plane_project_id="p",
                  github_repo="o/r", repo_dir=Path("/t"), worktrees_root=Path("/t"), poll_interval_seconds=0,
                  claude_binary="c", claude_model="m", mcp_config_path=Path("/t/m.json"), templates_dir=Path("/t"),
                  state_ids={"Ready to Dev": "rd", "In Progress": "ip", "Review": "rv", "QA": "qa",
                             "Completed": "done", "Deployed": "dep"}, max_concurrency=1)


class DepClient:
    def __init__(self, blocked_by, blocker_state):
        self._bb = blocked_by; self._bs = blocker_state
        from orchestrator.plane import Issue
        self._ready = [Issue("t1", "task", "", "rd", 1)]
        self.Issue = Issue
    def list_issues_in_state(self, s, per_page=25):
        return self._ready if s == "rd" else []
    def list_blocked_by(self, issue_id):
        return self._bb
    def get_issue(self, bid):
        return self.Issue(bid, "blk", "", self._bs, 2)


def test_poll_skips_ready_task_with_unfinished_blocker():
    from orchestrator.poller import poll_once, Ownership
    cfg = _cfg_with_states()
    client = DepClient(blocked_by=["b1"], blocker_state="ip")  # blocker In Progress -> not done
    dispatched = []
    poll_once(client, cfg, Ownership(), lambda i, r: dispatched.append(i.id))
    assert dispatched == []


def test_poll_dispatches_ready_task_when_blocker_done():
    from orchestrator.poller import poll_once, Ownership
    cfg = _cfg_with_states()
    client = DepClient(blocked_by=["b1"], blocker_state="done")  # blocker Completed
    dispatched = []
    poll_once(client, cfg, Ownership(), lambda i, r: dispatched.append(i.id))
    assert dispatched == ["t1"]


def test_poll_dispatches_ready_task_with_no_blockers():
    from orchestrator.poller import poll_once, Ownership
    cfg = _cfg_with_states()
    client = DepClient(blocked_by=[], blocker_state="ip")
    dispatched = []
    poll_once(client, cfg, Ownership(), lambda i, r: dispatched.append(i.id))
    assert dispatched == ["t1"]


# --- skip-review routing by work-type label ---
class FakeRouter(FakeClient):
    def __init__(self, by_state):
        super().__init__(by_state)
        self.moves = []
        self.comments = []

    def set_state(self, issue_id, state_id):
        self.moves.append((issue_id, state_id))

    def add_comment(self, issue_id, body):
        self.comments.append((issue_id, body))


def _review_states():
    return {"Ready to Dev": "s-ready", "Review": "s-review", "QA": "s-qa",
            "In Progress": "s-p", "Blocked": "s-b", "Completed": "s-d"}


def test_skip_review_label_auto_advances_to_qa_without_dispatch():
    cfg = make_cfg(_review_states(), concurrency=5)
    cfg.skip_review_labels = ["docs", "chore"]
    client = FakeRouter({"s-review": [Issue("i1", "a", "", "s-review", 1, labels=["docs"])]})
    own = Ownership()
    calls = []
    poll_once(client, cfg, own, lambda i, r: calls.append((i.id, r)))
    assert calls == []                          # no reviewer session launched
    assert client.moves == [("i1", "s-qa")]     # advanced straight to QA
    assert own.owns("i1") is False              # never claimed (no session to release)
    assert "auto-skipped review" in client.comments[0][1].lower()


def test_review_runs_when_label_not_in_skip_set():
    cfg = make_cfg(_review_states(), concurrency=5)
    cfg.skip_review_labels = ["docs"]
    client = FakeRouter({"s-review": [Issue("i2", "b", "", "s-review", 2, labels=["feature"])]})
    own = Ownership()
    calls = []
    poll_once(client, cfg, own, lambda i, r: calls.append((i.id, r)))
    assert calls == [("i2", "reviewer")]        # risky type still reviewed
    assert client.moves == []


# --- rework counting ---
from dataclasses import dataclass
from orchestrator.poller import rework_count


@dataclass
class _C:
    body_html: str


def test_rework_count_counts_only_reviewer_qa_bounces():
    comments = [
        _C("**[builder] Ready to Dev → In Progress** — starting"),   # not a bounce
        _C("**[builder] context loaded**"),                          # not a bounce
        _C("**[reviewer] Review → In Progress** — changes requested"),  # bounce
        _C("**[reviewer] Review → QA** — approved"),                  # not a bounce
        _C("**[qa] QA → In Progress** — QA failed"),                 # bounce
        _C("**[qa] QA → Completed** — merged"),                      # not a bounce
    ]
    assert rework_count(comments) == 2


def test_rework_count_is_case_insensitive_and_null_safe():
    assert rework_count([_C("[REVIEWER] REVIEW → IN PROGRESS"), _C(None)]) == 1


def test_run_executes_dispatches_in_parallel():
    import threading, time as _t
    from orchestrator.poller import run
    states = {"Ready to Dev": "s-ready", "In Progress": "s-prog", "Review": "s-rev",
              "QA": "s-qa", "Blocked": "s-blk", "Completed": "s-done"}
    cfg = make_cfg(states, concurrency=3)
    issues = [Issue(f"i{n}", f"t{n}", "", "s-ready", n) for n in range(3)]
    client = FakeClient({"s-ready": issues})
    lock = threading.Lock(); active = [0]; peak = [0]

    def dispatch(issue, role):
        with lock:
            active[0] += 1; peak[0] = max(peak[0], active[0])
        _t.sleep(0.15)
        with lock:
            active[0] -= 1

    run(cfg, client=client, dispatch=dispatch, sleep=lambda s: None, max_iterations=1)
    assert peak[0] == 3        # all three ran at the same time


def test_run_concurrency_one_stays_serial():
    from orchestrator.poller import run
    states = {"Ready to Dev": "s-ready", "In Progress": "s-prog", "Review": "s-rev",
              "QA": "s-qa", "Blocked": "s-blk", "Completed": "s-done"}
    cfg = make_cfg(states, concurrency=1)
    issues = [Issue(f"i{n}", f"t{n}", "", "s-ready", n) for n in range(3)]
    client = FakeClient({"s-ready": issues})
    order = []
    run(cfg, client=client, dispatch=lambda i, r: order.append(i.id),
        sleep=lambda s: None, max_iterations=1)
    assert order == ["i0"]     # gate stops at 1; only one claimed per poll
