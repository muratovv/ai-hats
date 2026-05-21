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

    HATS-337/follow-up: PEP 508 `name @ url` requires a URL scheme. For
    local-path AI_HATS_REPO_URL (e.g. `--local /path` in bootstrap.sh) we
    pass the path directly — pip detects pyproject.toml and installs.
    """
    url = _git_install_url()
    target = f"ai-hats @ {url}" if "://" in url else url
    return [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--force-reinstall",
        "--no-cache-dir",
        target,
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
            # `--no-merges`: hide `Merge branch 'task/hats-NNN'` titles —
            # conventional-commit titles from the actual work are more useful
            # than the wrapping merge commits under a no-ff merge convention.
            ["git", "-C", tmp, "log", "--oneline", "--no-merges", "-7"],
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
    from ..assembler import _builtin_library_layers
    from ..resolver import LibraryResolver
    from ..models import ComponentType

    paths = list(_builtin_library_layers())
    global_lib = Path.home() / ".ai-hats"
    if global_lib.is_dir():
        paths.append(global_lib)
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
    """Snapshot current role's rules and skills via composition.

    HATS-407: falls back to default_role when active_role is empty —
    fresh projects (post-init, pre-first-session) carry intent in
    default_role only.
    """
    from ..assembler import AssemblyError

    cfg = asm.project_config
    role = cfg.active_role or cfg.default_role
    if not role:
        return set(), set()
    try:
        result = asm.composer.compose(role, overlay=asm._get_overlay(role))
        return {r.name for r in result.rules}, {s.name for s in result.skills}
    except (AssemblyError, ValueError, OSError, KeyError, AttributeError):
        logger.debug("composition snapshot failed", exc_info=True)
        return set(), set()


@click.command()
@click.option(
    "--migrate-force",
    is_flag=True,
    help="Bypass v0.6 → v0.7 user-edit refusal during auto-bump "
    "(logs WARN per overwritten file).",
)
@click.option(
    "--check-branches",
    is_flag=True,
    help="Warn if local branches modify any v0.7-migration path slated for deletion.",
)
def update(migrate_force: bool, check_branches: bool):
    """Update ai-hats from GitHub.

    Auto-bumps after install. HATS-415: ``bump`` now self-heals v0.6 →
    v0.7 layouts transparently for the common case (no user edits). If
    user edits are detected on the v0.6 canonical files, the bump
    refuses with per-file guidance — re-run with ``--migrate-force``
    after relocating the content (or to overwrite). ``--check-branches``
    surfaces a warning when local branches modify the paths slated for
    deletion.
    """
    import subprocess

    from .. import __version__ as old_version
    from ..assembler import AssemblyError

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
        # HATS-408 review (R1): we used to call ``ProjectConfig.from_yaml``
        # AND ``_assembler`` (which itself calls ``from_yaml``), firing the
        # yaml-load WARNs (deprecated-field strip, default_role heal) twice
        # per ``self update``. Build the Assembler once and read its config.
        asm = _assembler(project_dir)
        cfg = asm.project_config
        # HATS-407: active_role is the runtime cache (empty until first
        # session). For a freshly-installed project where only default_role
        # is set, we still want auto-bump to run so migrations and the
        # canonical aggregator refresh. Fall back to default_role for the
        # bump-trigger decision.
        active_role = cfg.active_role or cfg.default_role or None
        if active_role:
            before_rules, before_skills = _snapshot_composition(asm)

    # 2. Install — wrapped in a Rich spinner so the terminal isn't silent
    # while pip downloads (can take 30s+ on slow links).
    cmd = _build_update_cmd()
    with console.status(
        "[cyan]Downloading ai-hats from GitHub …[/] "
        "[dim](pip install — may take a minute)[/]",
        spinner="dots",
    ):
        result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        console.print(f"[red]Update failed[/]: {result.stderr}")
        return

    # 2b. HATS-213 stage-2 verify: run a fresh interpreter against the
    # just-installed on-disk code, so any new declared runtime dep that
    # somehow didn't land gets healed before the user's next invocation.
    # Failures are non-fatal — layer A in cli.main() catches the rest.
    with console.status("[cyan]Verifying install …[/]", spinner="dots"):
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
    # standalone `ai-hats self migrate` was removed). HATS-400: when the
    # update actually changed the version on disk, run bump in a *fresh*
    # subprocess so the new code (migrations, healer, etc.) is loaded —
    # in-process `asm.bump()` would silently keep using the OLD code from
    # this update's interpreter, leaving the project half-fixed until the
    # user manually runs `ai-hats self bump` a second time.
    if active_role:
        console.print(f"\n[bold]Re-assembling:[/] {active_role}")
        version_changed = new_version != old_version
        if version_changed:
            # Fresh interpreter → new code (healer, migrations, etc.).
            # Stdout/stderr passthrough so [heal] lines / spinners stream live.
            bump_cmd = [sys.executable, "-m", "ai_hats", "self", "bump"]
            if migrate_force:
                bump_cmd.append("--migrate-force")
            if check_branches:
                bump_cmd.append("--check-branches")
            proc = subprocess.run(
                bump_cmd,
                cwd=str(project_dir),
                check=False,
            )
            if proc.returncode != 0:
                console.print(
                    f"  [yellow]Bump (fresh interpreter) exited "
                    f"{proc.returncode} — review output above[/]"
                )
            # Snapshot composition AFTER bump to compute rule/skill diff.
            asm = _assembler(project_dir)
            after_rules, after_skills = _snapshot_composition(asm)
        else:
            # No version change → no chicken-and-egg risk; in-process is fine
            # and avoids ~150ms subprocess overhead.
            try:
                asm = _assembler(project_dir)
                bump_result = asm.bump(
                    force_v07_migration=migrate_force,
                    check_v07_branches=check_branches,
                )
                if bump_result:
                    after_rules = {r.name for r in bump_result.rules}
                    after_skills = {s.name for s in bump_result.skills}
                    if bump_result.errors:
                        for err in bump_result.errors:
                            console.print(f"  [yellow]{err}[/]")
                else:
                    after_rules, after_skills = set(), set()
            except (AssemblyError, ValueError, OSError) as e:
                console.print(f"  [red]Bump failed[/]: {e}")
                after_rules, after_skills = before_rules, before_skills

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


# HATS-285: `ai-hats self migrate` removed. Migration is transparent inside
# `Assembler.set_role` / `Assembler.bump` (filesystem) and `ProjectConfig
# .from_yaml` (yaml). Cleanup of obsolete files lives in `Assembler.bump`.

# HATS-415: `ai-hats self migrate-v07` removed. The v0.6 → v0.7 migration
# is now inline in `Assembler.bump()` and exposed via
# `ai-hats self update --migrate-force` / `--check-branches` (also on
# `self bump` directly). Helpers (`migration_guidance`,
# `empty_composition`, `render_user_edits_refusal`) live in
# :mod:`ai_hats.migration_v07`; the Assembler owns hook-source and
# tier-2 source-lookup discovery as private methods.
