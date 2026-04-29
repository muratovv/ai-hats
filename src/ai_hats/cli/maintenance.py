"""`ai-hats update` / `ai-hats migrate` — self-maintenance of the tool."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from ._helpers import _assembler, _project_dir, console, logger


GIT_INSTALL_URL = "git+ssh://git@github.com/muratovv/ai-hats.git"


def _build_update_cmd() -> list[str]:
    """Build the pip command for updating ai-hats from GitHub."""
    return [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--force-reinstall",
        "--no-deps",
        "--no-cache-dir",
        f"ai-hats @ {GIT_INSTALL_URL}",
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

    # 1. Snapshot before update
    before_lib = _snapshot_library()
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

    # 4. Library diff
    after_lib = _snapshot_library()
    console.print("\n[bold]Library:[/]")
    if not _format_component_diff(before_lib, after_lib):
        console.print("  [dim]No changes[/]")

    # 5. Auto-migrate
    console.print("\n[bold]Migration:[/]")
    migrate.invoke(click.Context(migrate))

    # 6. Auto-bump if role active
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


@click.command()
def migrate():
    """Run migrations for ai-hats updates."""
    from ..models import ProjectConfig

    project_dir = _project_dir()
    config_path = project_dir / "ai-hats.yaml"

    if not config_path.exists():
        console.print("[yellow]No ai-hats.yaml found — nothing to migrate[/]")
        return

    config = ProjectConfig.from_yaml(config_path)
    current_version = config.schema_version

    # Run migrations in order
    migrations_applied = 0
    # Future: add migration functions here as schema evolves
    # if current_version < 2:
    #     _migrate_v1_to_v2(project_dir)
    #     current_version = 2
    #     migrations_applied += 1

    # Idempotent cleanup of files retired in the current release.
    cleanup_actions = _cleanup_obsolete_files(project_dir)
    for msg in cleanup_actions:
        console.print(f"  [green]✓[/] {msg}")

    if migrations_applied > 0:
        config.schema_version = current_version
        config.save(config_path)
        console.print(f"[green]Migrated[/] to schema version {current_version}")
    elif not cleanup_actions:
        console.print(f"[dim]Already at latest schema version ({current_version})[/]")


def _cleanup_obsolete_files(project_dir: Path) -> list[str]:
    """Delete files retired by the current release. Idempotent.

    Each entry: (relative path, human reason). Add new lines here when a
    release retires a generated file or directory.
    """
    obsolete = [
        (".agent/backlog.md", "removed legacy backlog.md (unified into STATE.md)"),
    ]
    actions: list[str] = []
    for rel, reason in obsolete:
        target = project_dir / rel
        if target.exists():
            target.unlink()
            actions.append(reason)
    return actions
