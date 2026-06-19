from pathlib import Path

T = Path("templates")
def read(n): return (T / n).read_text()


def test_hydration_recipe_lives_in_claude_md():
    c = read("CLAUDE.md.tmpl").lower()
    assert "comment trail" in c and "pr thread" in c and "git history" in c and "docs/" in c


def test_comment_reading_is_capped_not_full_every_time():
    for role in ("builder.md", "reviewer.md", "qa.md"):
        assert "since your last state move" in read(role).lower()


def test_builder_tag_is_not_malformed():
    b = read("builder.md")
    assert "→ <FROM-STATE>" not in b  # the broken FROM->FROM example is gone


def test_builder_and_qa_have_idempotency_guard():
    for role in ("builder.md", "qa.md"):
        assert "already moved" in read(role).lower()


def test_reviewer_and_qa_gate_on_ci():
    for role in ("reviewer.md", "qa.md"):
        assert "gh pr checks" in read(role).lower()


def test_all_roles_have_safety_section():
    for role in ("builder.md", "reviewer.md", "qa.md"):
        assert "safety" in read(role).lower()


def test_code_writing_roles_forbid_test_and_history_tampering():
    for role in ("builder.md", "qa.md"):
        body = read(role).lower()
        assert "force-push" in body
        assert "ci" in body and ("weaken" in body or "skip" in body)  # no green-washing


def test_qa_does_pre_merge_integration():
    q = read("qa.md").lower()
    assert "rebase" in q or "update-branch" in q or "current with trunk" in q
