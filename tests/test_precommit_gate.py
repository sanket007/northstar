import json, subprocess, os, stat
from pathlib import Path

GATE = Path("templates/hooks/precommit_gate.sh")


def run_gate(payload: dict, cwd: Path, env_extra: dict):
    env = {**os.environ, **env_extra}
    return subprocess.run(["bash", str(GATE.resolve())], input=json.dumps(payload),
                          text=True, capture_output=True, cwd=str(cwd), env=env)


def test_non_commit_command_passes(tmp_path):
    res = run_gate({"tool_name": "Bash", "tool_input": {"command": "ls -la"}},
                   tmp_path, {})
    assert res.returncode == 0


def test_commit_blocked_when_checks_fail(tmp_path):
    res = run_gate({"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}},
                   tmp_path, {"LINT_CMD": "false", "BUILD_CMD": "true", "TEST_CMD": "true",
                              "SKIP_MEMORY_CHECK": "1"})
    assert res.returncode == 2
    assert "lint" in (res.stdout + res.stderr).lower()


def test_commit_passes_when_checks_pass(tmp_path):
    res = run_gate({"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}},
                   tmp_path, {"LINT_CMD": "true", "BUILD_CMD": "true", "TEST_CMD": "true",
                              "SKIP_MEMORY_CHECK": "1"})
    assert res.returncode == 0
