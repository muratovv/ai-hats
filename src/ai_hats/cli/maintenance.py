"""`ai-hats self update` — self-maintenance of the tool."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click

from ._helpers import _assembler, _project_dir, console, logger


# HATS-337: AI_HATS_REPO_URL env overrides the default git URL, mirroring
# the bash launcher (HATS-339) so a single env var pins the install source
# end-to-end (CI, airgapped mirrors, custom forks).
def _git_install_url() -> str:
    return os.environ.get(
        "AI_HATS_REPO_URL", "git+ssh://git@github.com/muratovv/ai-hats.git"
    )


def _build_update_cmd() -> list[str]:
    """Build the pip command for updating ai-hats from GitHub.

    NOTE: we intentionally do NOT pass --no-deps. Dropping it means new
    dependencies declared in pyproject.toml (e.g. ptyprocess added in
    HATS-207) get pulled in on update; otherwise users hit
    ModuleNotFoundError at runtime after an update.
    """
    return [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--force-reinstall",
        "--no-cache-dir",
        f"ai-hats @ {_git_install_url()}",
    ]


def _get_installed_version() -> str:
    """Get the currently installed ai-hats version via subprocess.

    Uses a fresh Python process to avoid import caching.
    """
    import subprocess

    result = subprocess.run(
        [sys.executable, "-c", "from ai_hats import __version__; print(__version__)"],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _get_changelog() -> str:
    """Get recent commits from GitHub via shallow clone."""
    import subprocess
    import tempfile

    tmp = tempfile.mkdtemp(prefix="ai-hats-changelog-")
    try:
        result = subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "10",
                "--filter=blob:none",
                "--quiet",
                "ssh://git@github.com/muratovv/ai-hats.git",
                tmp,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return ""
        log = subprocess.run(
            ["git", "-C", tmp, "log", "--oneline", "-7"],
            capture_output=True,
            text=True,
        )
        return log.stdout.strip() if log.returncode == 0 else ""
    except (subprocess.SubprocessError, OSError):
        logger.debug("changelog fetch failed", exc_info=True)
        return ""
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


def _snapshot_dep_versions() -> dict[str, str]:
    """Snapshot ``{distribution_name: version}`` via a fresh ``pip list`` subprocess.

    Fresh subprocess avoids importlib cache divergence between pre- and
    post-update — important for HATS-213 activation banner.
    """
    import json
    import subprocess

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "list", "--format=json"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.SubprocessError, OSError):
        logger.debug("pip list snapshot failed", exc_info=True)
        return {}
    if result.returncode != 0:
        return {}
    try:
        items = json.loads(result.stdout or "[]")
    except (ValueError, TypeError):
        return {}
    return {item["name"].lower(): item.get("version", "") for item in items if "name" in item}


def _snapshot_library() -> dict[str, set[str]]:
    """Snapshot available component names from built-in + global library paths."""
    from ..library import LibraryResolver
    from ..models import ComponentType

    # __file__ lives in cli/; library lives in the parent package dir.
    builtin = Path(__file__).resolve().parent.parent / "libraries"
    paths = [p for p in [builtin, Path.home() / ".ai-hats"] if p.is_dir()]
    resolver = LibraryResolver(paths)
    return {ct.value: set(resolver.list_components(ct)) for ct in ComponentType}


def _format_component_diff(
    before: dict[str, set[str]],
    after: dict[str, set[str]],
) -> bool:
    """Print added/removed components. Returns True if any changes found."""
    any_changes = False
    for component_type in ("role", "trait", "rule", "skill"):
        old = before.get(component_type, set())
        new = after.get(component_type, set())
        added = sorted(new - old)
        removed = sorted(old - new)
        if added or removed:
            any_changes = True
            for name in added:
                console.print(f"  [green]+[/] {component_type}: {name}", highlight=False)
            for name in removed:
                console.print(f"  [red]-[/] {component_type}: {name}", highlight=False)
    return any_changes


def _snapshot_composition(asm) -> tuple[set[str], set[str]]:
    """Snapshot current role's rules and skills via composition."""
    from ..assembler import AssemblyError

    if not asm.project_config.active_role:
        return set(), set()
    try:
        result = asm.composer.compose(
            asm.project_config.active_role,
            overlay=asm._get_overlay(asm.project_config.active_role),
        )
        return {r.name for r in result.rules}, {s.name for s in result.skills}
    except (AssemblyError, ValueError, OSError, KeyError, AttributeError):
        logger.debug("composition snapshot failed", exc_info=True)
        return set(), set()


@click.command()
def update():
    """Update ai-hats from GitHub."""
    import subprocess

    from .. import __version__ as old_version
    from ..assembler import AssemblyError
    from ..models import ProjectConfig

    console.print(f"Current version: [bold]{old_version}[/]")
    # HATS-318: surface which interpreter we're updating. When the wrapper has
    # already re-exec'd into <ai_hats_dir>/.venv, the install goes to that env
    # by virtue of sys.executable; this banner makes the target unambiguous.
    if "/.venv/bin/python" in sys.executable:
        console.print(f"[dim]Target venv:[/] {sys.executable}")

    # 1. Snapshot before update
    before_lib = _snapshot_library()
    before_deps = _snapshot_dep_versions()
    project_dir = _project_dir()
    config_path = project_dir / "ai-hats.yaml"
    active_role = None
    before_rules: set[str] = set()
    before_skills: set[str] = set()

    if config_path.exists():
        config = ProjectConfig.from_yaml(config_path)
        active_role = config.active_role or None
        if active_role:
            asm = _assembler(project_dir)
            before_rules, before_skills = _snapshot_composition(asm)

    # 2. Install
    console.print("Updating from GitHub...")
    cmd = _build_update_cmd()
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        console.print(f"[red]Update failed[/]: {result.stderr}")
        return

    # 2b. HATS-213 stage-2 verify: run a fresh interpreter against the
    # just-installed on-disk code, so any new declared runtime dep that
    # somehow didn't land gets healed before the user's next invocation.
    # Failures are non-fatal — layer A in cli.main() catches the rest.
    verify = subprocess.run(
        [sys.executable, "-m", "ai_hats._bootstrap", "verify"],
        capture_output=True,
        text=True,
    )
    if verify.returncode != 0:
        warning = (verify.stderr or verify.stdout or "").strip() or "see logs"
        console.print(f"[yellow]Post-install verify warned[/]: {warning}")

    # 3. Version diff
    new_version = _get_installed_version()
    if new_version == old_version:
        console.print(f"[green]Already up to date[/] ({old_version})")
    else:
        console.print(f"[green]Updated[/]: {old_version} → [bold]{new_version}[/]")

        changelog = _get_changelog()
        if changelog:
            console.print("\n[bold]Recent changes:[/]")
            for line in changelog.splitlines()[:7]:
                console.print(f"  {line}")

    # 3b. Dep activation banner — flag the chicken-and-egg cycle: new in-
    # memory code is still the OLD one, so any changed dep won't be wired
    # until the next ai-hats invocation. (HATS-213)
    after_deps = _snapshot_dep_versions()
    dep_changes: list[str] = []
    for name, ver in after_deps.items():
        old = before_deps.get(name)
        if old is None:
            dep_changes.append(f"  [green]+[/] {name} {ver}")
        elif old != ver:
            dep_changes.append(f"  [cyan]~[/] {name} {old} → {ver}")
    for name in before_deps.keys() - after_deps.keys():
        dep_changes.append(f"  [red]-[/] {name}")
    if dep_changes:
        console.print("\n[bold]Dependency activation:[/]")
        for line in dep_changes:
            console.print(line, highlight=False)
        console.print("  Restart your shell or run any 'ai-hats' command to activate new deps.")
        console.print("  If anything misbehaves, run: ai-hats   (it will self-heal)")

    # 4. Library diff
    after_lib = _snapshot_library()
    console.print("\n[bold]Library:[/]")
    if not _format_component_diff(before_lib, after_lib):
        console.print("  [dim]No changes[/]")

    # 5. Auto-bump if role active (HATS-285: migration runs inside bump now;
    # standalone `ai-hats self migrate` was removed).
    if active_role:
        console.print(f"\n[bold]Re-assembling:[/] {active_role}")
        try:
            asm = _assembler(project_dir)
            bump_result = asm.bump()
            if bump_result:
                after_rules = {r.name for r in bump_result.rules}
                after_skills = {s.name for s in bump_result.skills}
                added_r = sorted(after_rules - before_rules)
                removed_r = sorted(before_rules - after_rules)
                added_s = sorted(after_skills - before_skills)
                removed_s = sorted(before_skills - after_skills)
                has_diff = bool(added_r or removed_r or added_s or removed_s)
                if has_diff:
                    for r in added_r:
                        console.print(f"  [green]+[/] rule: {r}", highlight=False)
                    for r in removed_r:
                        console.print(f"  [red]-[/] rule: {r}", highlight=False)
                    for s in added_s:
                        console.print(f"  [green]+[/] skill: {s}", highlight=False)
                    for s in removed_s:
                        console.print(f"  [red]-[/] skill: {s}", highlight=False)
                else:
                    console.print("  [dim]No composition changes[/]")
                if bump_result.errors:
                    for err in bump_result.errors:
                        console.print(f"  [yellow]{err}[/]")
        except (AssemblyError, ValueError, OSError) as e:
            console.print(f"  [red]Bump failed[/]: {e}")


# HATS-285: `ai-hats self migrate` removed. Migration is transparent inside
# `Assembler.set_role` / `Assembler.bump` (filesystem) and `ProjectConfig
# .from_yaml` (yaml). Cleanup of obsolete files lives in `Assembler.bump`.
