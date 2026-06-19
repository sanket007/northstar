"""Strong per-language formatting/lint enforcement, opt-in at `northstar project add`.

For each supported language we ship a strict config (templates/formatting/<lang>/), install the
tooling as dev dependencies, and fold a format+lint **check** command into the project's lint gate
so the pre-commit hook and the trunk-health verify both reject unformatted or lint-failing code.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import shutil

from northstar.assets import templates_dir
from northstar.proc import run


@dataclass(frozen=True)
class FormattingSpec:
    language: str
    detect_files: tuple[str, ...]   # any present in the repo → this language
    config_files: tuple[str, ...]   # copied from templates/formatting/<language>/ into the repo
    install_cmd: object             # list[str] or shell string (dev-dependency install)
    check_cmd: str                  # the gate: lint + format-check (non-zero = reject)
    fix_cmd: str                    # how an agent auto-fixes before committing


SPECS: dict[str, FormattingSpec] = {
    "javascript": FormattingSpec(
        language="javascript",
        detect_files=("package.json",),
        config_files=("eslint.config.js", ".prettierrc.json"),
        install_cmd=["npm", "install", "-D", "eslint", "@eslint/js", "globals",
                     "prettier", "eslint-config-prettier"],
        check_cmd="npx --no-install eslint . && npx --no-install prettier --check .",
        fix_cmd="npx eslint . --fix && npx prettier --write ."),
    "python": FormattingSpec(
        language="python",
        detect_files=("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"),
        config_files=("ruff.toml",),
        install_cmd=["python3", "-m", "pip", "install", "ruff"],
        check_cmd="ruff check . && ruff format --check .",
        fix_cmd="ruff check . --fix && ruff format ."),
    "go": FormattingSpec(
        language="go",
        detect_files=("go.mod",),
        config_files=(".golangci.yml",),
        install_cmd=("go install mvdan.cc/gofumpt@latest && "
                     "go install github.com/golangci/golangci-lint/v2/cmd/golangci-lint@latest"),
        # quote-free so it survives being embedded in the LINT_CMD="..." hook env;
        # `gofumpt -l .` lists unformatted files, the negated grep fails the gate if any exist.
        check_cmd="gofumpt -l . | (! grep -q .) && golangci-lint run",
        fix_cmd="gofumpt -w . && golangci-lint run --fix"),
}

SUPPORTED = tuple(SPECS)


def detect_language(repo_dir) -> str | None:
    """Best-effort language detection from marker files in the repo root."""
    repo = Path(repo_dir)
    for spec in SPECS.values():
        if any((repo / marker).exists() for marker in spec.detect_files):
            return spec.language
    return None


def install_formatting(repo_dir, language, *, runner=run, install_deps=True) -> FormattingSpec:
    """Copy the language's config into the repo and (optionally) install its tooling.

    Returns the FormattingSpec so the caller can fold spec.check_cmd into the lint gate.
    """
    spec = SPECS[language]
    src = templates_dir() / "formatting" / language
    repo = Path(repo_dir)
    for name in spec.config_files:
        shutil.copyfile(src / name, repo / name)
    if install_deps:
        cmd = spec.install_cmd
        runner(cmd, shell=isinstance(cmd, str), cwd=repo)
    return spec
