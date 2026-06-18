import subprocess
from pathlib import Path
from orchestrator.worktree import create_worktree, remove_worktree


def _init_repo(path: Path):
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "README.md").write_text("seed\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.email=t@t.t",
                    "-c", "user.name=t", "commit", "-qm", "init"], check=True)


def test_create_and_remove_worktree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    roots = tmp_path / "wt"

    wt = create_worktree(repo, roots, "proj-7")
    assert wt.exists()
    assert (wt / "README.md").exists()
    branches = subprocess.run(["git", "-C", str(repo), "branch", "--list", "agent/proj-7"],
                              capture_output=True, text=True).stdout
    assert "agent/proj-7" in branches

    remove_worktree(repo, wt)
    assert not wt.exists()
