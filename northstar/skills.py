from __future__ import annotations
from dataclasses import dataclass
import json

from northstar.proc import run


@dataclass(frozen=True)
class Plugin:
    name: str
    marketplace: str
    add_source: str


@dataclass(frozen=True)
class Native:
    name: str
    kind: str   # "script" | "npx"
    cmd: str


PLUGINS = [
    Plugin("superpowers", "claude-plugins-official", "anthropics/claude-plugins-official"),
    Plugin("frontend-design", "claude-plugins-official", "anthropics/claude-plugins-official"),
    Plugin("playwright", "claude-plugins-official", "anthropics/claude-plugins-official"),
    Plugin("andrej-karpathy-skills", "karpathy-skills", "multica-ai/andrej-karpathy-skills"),
]

NATIVE = [
    Native("caveman", "script",
           "curl -fsSL https://raw.githubusercontent.com/JuliusBrussee/caveman/main/install.sh | bash"),
    Native("grill-me", "npx", "npx --yes skills@latest add mattpocock/skills"),
]


def marketplaces() -> list[str]:
    seen, out = set(), []
    for p in PLUGINS:
        if p.add_source not in seen:
            seen.add(p.add_source)
            out.append(p.add_source)
    return out


def installed_plugins(runner=run) -> dict[str, str]:
    res = runner(["claude", "plugin", "list", "--json"])
    if not res.ok:
        return {}
    try:
        data = json.loads(res.stdout or "[]")
    except json.JSONDecodeError:
        return {}
    rows = data if isinstance(data, list) else data.get("plugins", [])
    return {r["name"]: r.get("version", "") for r in rows if "name" in r}


def _first_line(res) -> str:
    text = (res.stderr or res.stdout or "").strip()
    return text.splitlines()[0] if text else "failed"


def install_all(runner=run, log=print) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    for src in marketplaces():
        log(f"  → marketplace add {src}")
        runner(["claude", "plugin", "marketplace", "add", src])
    log("  → marketplace update (refreshing catalogs to latest)")
    runner(["claude", "plugin", "marketplace", "update"])
    present = installed_plugins(runner=runner)
    for p in PLUGINS:
        ref = f"{p.name}@{p.marketplace}"
        if p.name not in present:
            log(f"  → installing {ref}")
            runner(["claude", "plugin", "install", ref, "--scope", "user"])
        else:
            log(f"  → {p.name} already present; updating")
        upd = runner(["claude", "plugin", "update", ref, "--scope", "user"])
        log(f"    {'✓' if upd.ok else '⚠'} {p.name}"
            + ("" if upd.ok else f": {_first_line(upd)}"))
        results.append((p.name, upd.ok, "plugin"))
    for n in NATIVE:
        log(f"  → installing {n.name} (native installer)")
        res = runner(n.cmd, shell=True)
        log(f"    {'✓' if res.ok else '⚠'} {n.name}"
            + ("" if res.ok else f": {_first_line(res)} — run manually: {n.cmd}"))
        results.append((n.name, res.ok, "native"))
    return results


def verify(runner=run) -> list[tuple[str, bool]]:
    installed = installed_plugins(runner=runner)
    return [(p.name, p.name in installed) for p in PLUGINS]
