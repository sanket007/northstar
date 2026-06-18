from __future__ import annotations
from pathlib import Path
import os
import yaml


def home() -> Path:
    return Path(os.environ.get("NORTHSTAR_HOME", str(Path.home() / ".northstar")))


def projects_dir() -> Path:
    return home() / "projects"


def logs_dir() -> Path:
    return home() / "logs"


def project_config_path(name: str) -> Path:
    return projects_dir() / f"{name}.yaml"


def log_path(name: str) -> Path:
    return logs_dir() / f"{name}.log"


def registry_path() -> Path:
    return home() / "registry.yaml"


def ensure_dirs() -> None:
    for d in (home(), projects_dir(), logs_dir()):
        d.mkdir(parents=True, exist_ok=True)


def load_registry() -> dict:
    p = registry_path()
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text()) or {}


def save_registry(reg: dict) -> None:
    ensure_dirs()
    registry_path().write_text(yaml.safe_dump(reg, sort_keys=True))


def register_project(name: str, meta: dict) -> None:
    reg = load_registry()
    reg[name] = meta
    save_registry(reg)


def unregister_project(name: str) -> None:
    reg = load_registry()
    reg.pop(name, None)
    save_registry(reg)


def list_projects() -> dict:
    return load_registry()
