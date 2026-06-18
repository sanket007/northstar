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
