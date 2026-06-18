from northstar.proc import CommandResult
from northstar.doctor import run_checks, all_critical_ok


def fake_runner_factory(table):
    # table maps the FIRST token of cmd -> CommandResult
    def runner(cmd, **kw):
        key = (cmd if isinstance(cmd, str) else cmd[0])
        first = key.split()[0] if isinstance(key, str) else key
        return table.get(first, CommandResult(127, "", "not found"))
    return runner


def test_all_present_passes():
    ok = CommandResult(0, "v1", "")
    table = {t: ok for t in ["python3", "git", "gh", "claude", "uvx", "tmux", "npx"]}
    table["gh"] = CommandResult(0, "Logged in to github.com", "")
    checks = run_checks(runner=fake_runner_factory(table))
    assert all_critical_ok(checks) is True


def test_missing_tmux_is_critical_failure():
    ok = CommandResult(0, "v1", "")
    table = {t: ok for t in ["python3", "git", "gh", "claude", "uvx", "npx"]}
    table["gh"] = CommandResult(0, "Logged in", "")
    # tmux absent -> 127
    checks = run_checks(runner=fake_runner_factory(table))
    tmux = next(c for c in checks if c.name == "tmux")
    assert tmux.ok is False and tmux.critical is True
    assert all_critical_ok(checks) is False


def test_gh_unauthenticated_fails_github_check():
    ok = CommandResult(0, "v1", "")
    table = {t: ok for t in ["python3", "git", "gh", "claude", "uvx", "tmux", "npx"]}
    table["gh"] = CommandResult(1, "", "not logged in")  # gh present but auth fails
    checks = run_checks(runner=fake_runner_factory(table))
    gh_auth = next(c for c in checks if c.name == "github-auth")
    assert gh_auth.ok is False and gh_auth.critical is True
