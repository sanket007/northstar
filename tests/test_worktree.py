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


def _git(path, *args):
    subprocess.run(["git", "-C", str(path), "-c", "user.email=t@t.t", "-c", "user.name=t",
                    *args], check=True, capture_output=True)


def test_create_worktree_branches_from_fresh_origin_main(tmp_path):
    # origin (bare) ← seed clone pushes main; repo clones; then origin/main advances.
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True)
    (seed / "README.md").write_text("seed\n")
    _git(seed, "add", "."); _git(seed, "commit", "-qm", "init"); _git(seed, "push", "-q", "origin", "main")

    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True)

    # a sibling merges to trunk AFTER repo was cloned → repo's local main is now stale
    (seed / "LATEST.txt").write_text("merged\n")
    _git(seed, "add", "."); _git(seed, "commit", "-qm", "sibling"); _git(seed, "push", "-q", "origin", "main")

    wt = create_worktree(repo, tmp_path / "wt", "x-builder", base_branch="main")
    assert (wt / "LATEST.txt").exists()  # built on fresh trunk, not stale local HEAD


def test_create_worktree_resumes_pushed_wip(tmp_path):
    # A prior session pushed progress to origin/agent/x-builder; a max_turns continuation
    # must resume from it, not reset the branch back to trunk (which would lose the work).
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True)
    (seed / "README.md").write_text("seed\n")
    _git(seed, "add", "."); _git(seed, "commit", "-qm", "init"); _git(seed, "push", "-q", "origin", "main")

    # prior session's pushed WIP on the agent branch
    _git(seed, "checkout", "-qb", "agent/x-builder")
    (seed / "WIP.txt").write_text("partial progress\n")
    _git(seed, "add", "."); _git(seed, "commit", "-qm", "wip")
    _git(seed, "push", "-q", "origin", "agent/x-builder")

    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True)

    wt = create_worktree(repo, tmp_path / "wt", "x-builder", base_branch="main")
    assert (wt / "WIP.txt").exists()  # resumed from pushed progress, not reset to trunk
