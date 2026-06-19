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
