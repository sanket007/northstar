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
