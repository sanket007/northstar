from pathlib import Path
import textwrap
from orchestrator.config import load_config


def test_load_config_parses_all_fields(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(textwrap.dedent("""
        plane_base_url: https://plane.example.com
        plane_api_key: key-123
        plane_workspace_slug: acme
        plane_project_id: proj-uuid
        github_repo: acme/sandbox
        repo_dir: /tmp/sandbox
        worktrees_root: /tmp/worktrees
        poll_interval_seconds: 30
        claude_binary: claude
        claude_model: claude-opus-4-8
        mcp_config_path: /tmp/plane-mcp.json
        templates_dir: /tmp/templates
        max_concurrency: 1
        state_ids:
          "Ready to Dev": s-ready
          "In Progress": s-prog
          "Review": s-review
          "QA": s-qa
          "Blocked": s-blocked
          "Completed": s-done
    """))
    cfg = load_config(cfg_file)
    assert cfg.plane_base_url == "https://plane.example.com"
    assert cfg.max_concurrency == 1
    assert cfg.worktrees_root == Path("/tmp/worktrees")
    assert cfg.state_ids["QA"] == "s-qa"
    # optional fields default when not in yaml
    assert cfg.session_timeout_seconds == 1800
    assert cfg.max_turns == 200
    assert cfg.max_turn_retries == 4
    assert cfg.usage_limit_cooldown_seconds == 900
    assert cfg.skip_review_labels == []


def test_load_config_optional_turn_time_cap(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(textwrap.dedent("""
        plane_base_url: https://plane.example.com
        plane_api_key: key-123
        plane_workspace_slug: acme
        plane_project_id: proj-uuid
        github_repo: acme/sandbox
        repo_dir: /tmp/sandbox
        worktrees_root: /tmp/worktrees
        poll_interval_seconds: 30
        claude_binary: claude
        claude_model: claude-opus-4-8
        mcp_config_path: /tmp/plane-mcp.json
        templates_dir: /tmp/templates
        state_ids:
          "Blocked": s-blocked
        session_timeout_seconds: 600
        max_turns: 10
    """))
    cfg = load_config(cfg_file)
    assert cfg.session_timeout_seconds == 600
    assert cfg.max_turns == 10


def test_load_config_missing_required_key_raises(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("plane_base_url: https://x\n")
    try:
        load_config(cfg_file)
        assert False, "expected KeyError"
    except KeyError:
        pass
