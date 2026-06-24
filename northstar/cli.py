from __future__ import annotations
from pathlib import Path
import typer

from northstar import doctor, project, supervisor, paths, importer, proc
from northstar.initcmd import do_init

app = typer.Typer(help="northstar — autonomous dev orchestrator CLI", no_args_is_help=True)
project_app = typer.Typer(help="manage projects")
app.add_typer(project_app, name="project")


# Registered as the `doctor` command. The function is NOT named `doctor` because
# `doctor` is the imported module (the test monkeypatches `cli.doctor.run_checks`).
@app.command(name="doctor")
def doctor_cmd(deep: bool = typer.Option(False, "--deep")):
    """Check prerequisites."""
    checks = doctor.run_checks(deep=deep)
    for c in checks:
        mark = "✓" if c.ok else "✗"
        line = f"  {mark} {c.name}: {c.detail}"
        if not c.ok:
            line += f" — {c.fix}"
        typer.echo(line)
    raise typer.Exit(0 if doctor.all_critical_ok(checks) else 1)


@app.command()
def init(deep: bool = typer.Option(False, "--deep"),
         backend: str = typer.Option("auto", "--backend",
                                      help="process backend: auto|tmux|detached")):
    """Set up this machine (checks + install skills to latest)."""
    resolved = backend
    if backend == "auto":
        if proc.run(["tmux", "-V"]).ok:
            resolved = "tmux"
        else:
            typer.echo(
                "tmux not found.\n"
                "  • tmux: live-attach to the running session (needs tmux installed)\n"
                "  • detached: no extra dependency; logs via file (no live attach)\n"
                "  Both survive your terminal closing; neither survives a reboot.")
            if typer.confirm("Use the built-in detached backend?", default=True):
                resolved = "detached"
            else:
                typer.echo("Aborted. Install tmux, or re-run with --backend detached.")
                raise typer.Exit(1)
    raise typer.Exit(do_init(deep=deep, backend=resolved))


@project_app.command("list")
def project_list():
    for name, meta in paths.list_projects().items():
        typer.echo(f"  {name}  {meta.get('github_repo','')}")


@project_app.command("remove")
def project_remove(name: str):
    paths.unregister_project(name)
    typer.echo(f"removed {name}")


@project_app.command("add")
def project_add(
    name: str = typer.Option(..., prompt=True),
    plane_base_url: str = typer.Option(..., prompt=True),
    plane_api_key: str = typer.Option(..., prompt=True, hide_input=True),
    plane_workspace_slug: str = typer.Option(..., prompt=True),
    new_plane_project: bool = typer.Option(False, "--new-plane-project/--existing-plane-project",
                                           prompt="Create a NEW Plane project?"),
    plane_project_id: str = typer.Option("", "--plane-project-id"),
    plane_project_name: str = typer.Option("", "--plane-project-name"),
    plane_identifier: str = typer.Option("", "--plane-identifier"),
    github_repo: str = typer.Option(..., prompt="GitHub repo (owner/name)"),
    repo_dir: Path = typer.Option(..., prompt="Local path for the repo"),
    lint_cmd: str = typer.Option("npm run lint", prompt=True),
    build_cmd: str = typer.Option("npm run build", prompt=True),
    test_cmd: str = typer.Option("npm test", prompt=True),
    max_concurrency: int = typer.Option(
        3, "--concurrency",
        prompt="How many tasks to build in parallel? (each runs its own Claude session + git worktree)"),
    enforce_formatting: bool = typer.Option(
        True, "--formatting/--no-formatting",
        prompt="Impose strong formatting + lint rules if the project language is supported "
               "(JavaScript/TypeScript, Python, Go)?"),
    create_if_missing: bool = typer.Option(False, "--create"),
):
    """Add or link a project (sets up the Plane project + board)."""
    import re
    if new_plane_project:
        if not plane_project_name:
            plane_project_name = typer.prompt("Plane project name")
        if not plane_identifier:
            plane_identifier = typer.prompt("Plane project identifier (short, UPPERCASE)")
        # Normalize to Plane's rule (UPPERCASE, alphanumeric, <=12) so a hyphen/lowercase just works.
        normalized = re.sub(r"[^A-Za-z0-9]", "", plane_identifier).upper()[:12]
        if not normalized:
            typer.echo("✗ Plane project identifier needs at least one letter/number.")
            raise typer.Exit(1)
        if normalized != plane_identifier:
            typer.echo(f"  (using identifier '{normalized}')")
        plane_identifier = normalized
    else:
        if not plane_project_id:
            plane_project_id = typer.prompt("Existing Plane project id")
    inp = project.ProjectInputs(
        name=name, plane_base_url=plane_base_url, plane_api_key=plane_api_key,
        plane_workspace_slug=plane_workspace_slug, plane_project_id=plane_project_id,
        github_repo=github_repo, repo_dir=repo_dir,
        lint_cmd=lint_cmd, build_cmd=build_cmd, test_cmd=test_cmd,
        max_concurrency=max(1, max_concurrency),
        enforce_formatting=enforce_formatting,
        plane_new_project=new_plane_project, plane_project_name=plane_project_name,
        plane_identifier=plane_identifier)
    try:
        meta = project.add_project(inp, create_if_missing=create_if_missing)
    except (ValueError, RuntimeError) as e:
        typer.echo(f"✗ {e}")
        raise typer.Exit(1)
    typer.echo(f"added {name}: {meta['github_repo']}")
    if enforce_formatting:
        lang = meta.get("formatting")
        typer.echo(f"  formatting: enforced for {lang} (config written, tooling installed, "
                   "commit gate updated)" if lang
                   else "  formatting: no supported language detected — skipped")


@app.command()
def start(name: str):
    rt = paths.load_project(name)
    supervisor.start(name, rt.repo_dir, rt.plane_env)
    typer.echo(f"started ns-{name}")


@app.command()
def stop(name: str):
    supervisor.stop(name)
    typer.echo(f"stopped ns-{name}")


@app.command()
def restart(name: str):
    rt = paths.load_project(name)
    supervisor.restart(name, rt.repo_dir, rt.plane_env)
    typer.echo(f"restarted ns-{name}")


@app.command()
def status():
    rows = supervisor.status(list(paths.list_projects()))
    for r in rows:
        typer.echo(f"  {'● running' if r['running'] else '○ stopped'}  {r['name']}")


@app.command()
def logs(name: str, follow: bool = typer.Option(False, "-f", "--follow")):
    import subprocess
    subprocess.run(supervisor.logs_command(name, follow))


plan_app = typer.Typer(help="import plans into Plane")
app.add_typer(plan_app, name="plan")


@plan_app.command("import")
def plan_import(project: str, plan_path: str):
    """Grill a plan and create Plane Draft tasks (interactive)."""
    importer.run_import(project, plan_path)
