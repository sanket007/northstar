from __future__ import annotations
from pathlib import Path
import os
import shutil


def assets_root() -> Path:
    override = os.environ.get("NORTHSTAR_ASSETS_DIR")
    if override:
        return Path(override)
    # northstar/assets.py -> repo root holding templates/ and plane-mcp.json
    return Path(__file__).resolve().parent.parent


def templates_dir() -> Path:
    return assets_root() / "templates"


def plane_mcp_json() -> Path:
    return assets_root() / "plane-mcp.json"


def copy_plane_mcp_to(dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / "plane-mcp.json"
    shutil.copyfile(plane_mcp_json(), out)
    return out
