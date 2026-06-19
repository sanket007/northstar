from orchestrator import health
from tests.test_dispatch import make_cfg


def test_verify_main_skips_when_no_verify_cmd(tmp_path):
    cfg = make_cfg(tmp_path, verify_cmd=None)
    ok, detail = health.verify_main(cfg)
    assert ok is True and "skip" in detail.lower()


def test_verify_main_runs_in_fresh_worktree_and_reports_failure(tmp_path):
    cfg = make_cfg(tmp_path, verify_cmd="exit 1")
    made, removed = [], []

    def mk(repo_dir, roots, slug, base_branch="main"):
        made.append((slug, base_branch))
        return roots / slug

    ok, detail = health.verify_main(
        cfg, mk_worktree=mk, rm_worktree=lambda r, w: removed.append(w),
        run_shell=lambda cmd, cwd: (False, "boom"))
    assert ok is False and detail == "boom"
    assert made == [("main-health", "main")]          # ephemeral worktree off trunk
    assert len(removed) == 1                           # always cleaned up


def test_verify_main_reports_success(tmp_path):
    cfg = make_cfg(tmp_path, verify_cmd="true")
    ok, detail = health.verify_main(
        cfg, mk_worktree=lambda *a, **k: tmp_path / "x",
        rm_worktree=lambda r, w: None,
        run_shell=lambda cmd, cwd: (True, "all green"))
    assert ok is True and detail == "all green"
