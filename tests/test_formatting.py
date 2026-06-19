from pathlib import Path
import pytest

from northstar import formatting
from northstar.proc import CommandResult


# --- detection ---
@pytest.mark.parametrize("marker,lang", [
    ("package.json", "javascript"),
    ("pyproject.toml", "python"),
    ("requirements.txt", "python"),
    ("go.mod", "go"),
])
def test_detect_language(tmp_path, marker, lang):
    (tmp_path / marker).write_text("x")
    assert formatting.detect_language(tmp_path) == lang


def test_detect_language_none_when_unknown(tmp_path):
    (tmp_path / "README.md").write_text("hi")
    assert formatting.detect_language(tmp_path) is None


# --- templates exist and are non-trivial ---
@pytest.mark.parametrize("lang", formatting.SUPPORTED)
def test_each_language_has_real_config_templates(lang):
    src = formatting.templates_dir() / "formatting" / lang
    for name in formatting.SPECS[lang].config_files:
        f = src / name
        assert f.exists() and f.stat().st_size > 0, f"{f} missing/empty"


def test_no_check_cmd_has_double_quotes():
    # check_cmd is embedded inside LINT_CMD="..." in the hook env — double quotes would break it.
    for spec in formatting.SPECS.values():
        assert '"' not in spec.check_cmd, f"{spec.language} check_cmd has a double quote"


# --- install_formatting ---
def test_install_formatting_copies_configs_and_installs(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    calls = []

    def runner(cmd, **kw):
        calls.append((cmd, kw.get("cwd")))
        return CommandResult(0, "", "")

    spec = formatting.install_formatting(repo, "python", runner=runner)
    assert (repo / "ruff.toml").exists()
    assert spec.check_cmd.startswith("ruff")
    assert len(calls) == 1                       # the install ran...
    assert Path(str(calls[0][1])) == repo        # ...in the repo dir


def test_install_formatting_can_skip_deps(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    calls = []
    formatting.install_formatting(repo, "javascript", runner=lambda *a, **k: calls.append(a),
                                  install_deps=False)
    assert (repo / "eslint.config.js").exists()
    assert (repo / ".prettierrc.json").exists()
    assert calls == []                           # no tooling installed
