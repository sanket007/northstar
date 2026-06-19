import os
import pytest

from orchestrator import obs


def test_redacts_env_style_secrets():
    s = obs.redact("env PLANE_API_KEY=plane_abc123 python -m orchestrator")
    assert "plane_abc123" not in s
    assert "PLANE_API_KEY=***" in s


@pytest.mark.parametrize("key", ["API_KEY", "PLANE_TOKEN", "DB_PASSWORD", "MY_SECRET"])
def test_redacts_all_secret_keywords(key):
    assert f"{key}=***" in obs.redact(f"{key}=hunter2")


def test_redacts_github_pats():
    assert "ghp_" not in obs.redact("git push https://ghp_0123456789ABCDEFabcdef00 @x")
    assert "github_pat_" not in obs.redact("token github_pat_11ABCDEFG0123456789_abcdefXYZ")


def test_format_cmd_redacts_list_and_string():
    assert "***" in obs.format_cmd(["env", "PLANE_API_KEY=secretval", "claude"])
    assert "secretval" not in obs.format_cmd("env PLANE_API_KEY=secretval claude")


def test_quiet_suppresses_output(capsys, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_QUIET", "1")
    obs.info("exec", "should not appear")
    assert capsys.readouterr().err == ""


def test_emits_to_stderr_by_default(capsys, monkeypatch):
    monkeypatch.delenv("NORTHSTAR_QUIET", raising=False)
    obs.info("plane", "hello")
    captured = capsys.readouterr()
    assert "plane" in captured.err and "hello" in captured.err
    assert captured.out == ""


def test_short_url_hides_host_keeps_path(monkeypatch):
    monkeypatch.delenv("NORTHSTAR_DEBUG", raising=False)
    out = obs._short_url("https://plane.example.com/api/v1/workspaces/ws/projects/")
    assert out.startswith("/api/v1/")
    assert "plane.example.com" not in out
