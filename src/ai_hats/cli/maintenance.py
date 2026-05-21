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
def update():
    """Update ai-hats from GitHub."""
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
            proc = subprocess.run(
                [sys.executable, "-m", "ai_hats", "self", "bump"],
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
                bump_result = asm.bump()
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


# ----- HATS-408: ai-hats self migrate-v07 -----


def _migration_guidance(tier: int, kind: str, path_name: str) -> str:
    """Human-readable pointer to the new home for a v0.6 finding."""
    if tier == 1:
        return (
            "Move project-wide content to [bold].agent/ai-hats/user-rules/<name>.md[/]; "
            "move role-specific content to "
            "[bold].agent/ai-hats/library/usage/{traits,rules,skills,roles}/...[/]."
        )
    if tier == 2:
        bucket = {
            "lib_rule_dir": "rules",
            "lib_skill_dir": "skills",
            "lib_hook_dir": "hooks",
        }.get(kind, "rules")
        return (
            f"Move overrides to [bold].agent/ai-hats/library/usage/{bucket}/{path_name}/...[/]."
        )
    return ""


def _empty_composition():
    """Return an empty CompositionResult for projects without an effective role.

    Used when both ``active_role`` and ``default_role`` are unset — no role to
    compose, so any Tier-1 file on disk is by definition baselineless and
    classifies as a user edit (conservative — release-gate fork A).
    """
    from ..composer import CompositionResult
    from ..models import HooksConfig

    return CompositionResult(
        name="",
        priorities=[],
        rules=[],
        skills=[],
        hooks=HooksConfig(),
        injections=[],
    )


def _build_tier2_source_lookup(asm) -> dict[str, Path]:
    """Map mirror-dir name → source root for every rule/skill the composer resolved.

    Used by ``migration_v07.plan_migration`` so Tier-2 dirs whose source we
    can locate get a diff baseline (safe-to-delete when content matches).
    """
    from ..models import resolve_namespace

    resolver = asm.composer.resolver
    # Snapshot the *full* current composition so we catch rules/skills the
    # active role doesn't directly use but that the v0.6 mirror once held.
    effective = asm.project_config.active_role or asm.project_config.default_role
    if not effective:
        return {}
    try:
        comp = asm.composer.compose(effective, overlay=asm._get_overlay(effective))
    except Exception:  # noqa: BLE001 — defensive: any compose failure → empty lookup
        logger.debug("composition for tier2 lookup failed", exc_info=True)
        return {}
    out: dict[str, Path] = {}
    for r in comp.rules:
        src = resolver.resolve_rule_dir(r.name)
        if src is not None:
            # Mirror-dir name on disk uses fs-namespace form (`a::b` → `a/b`),
            # but the directory baseline match uses the mirror's own basename
            # which is the leaf name. Index both for safety.
            out[r.name] = src
            out[resolve_namespace(r.name).rsplit("/", 1)[-1]] = src
    for s in comp.skills:
        src = resolver.resolve_skill_dir(s.name)
        if src is not None:
            out[s.name] = src
            out[resolve_namespace(s.name).rsplit("/", 1)[-1]] = src
    return out


_MIGRATE_COMMIT_MESSAGE = (
    "chore(v0.7): migrate to dynamic role composition\n"
    "\n"
    "Delete materialized role-content and library copies under\n"
    ".agent/ai-hats/; composition is now per-session in memory.\n"
    "See HATS-294 + CHANGELOG."
)


@click.command("migrate-v07")
@click.option(
    "--force",
    is_flag=True,
    help="Bypass user-edit refusal (logs WARN per overwritten file).",
)
@click.option(
    "--no-commit",
    is_flag=True,
    help="Stage migration changes but do not create the git commit.",
)
@click.option(
    "--check-branches",
    is_flag=True,
    help="Warn if local branches modify any path that will be deleted.",
)
def migrate_v07(force: bool, no_commit: bool, check_branches: bool):
    """One-shot safe migration from v0.6 materialised layout to v0.7 per-session compose.

    Inspects every v0.6 on-disk artefact (priorities.md, role.md, traits/*.md,
    rules/*.md, skills_index.md, library/{rules,skills,hooks}/<name>/), diffs
    each vs a freshly composed baseline, refuses with guidance on any user
    edit (--force bypasses with WARN per file). On success: deletes stale
    files, regenerates the v0.7 imports.md aggregator, persists yaml
    hardening (deprecated-field strip + default_role heal), commits in a
    single atomic envelope.

    Idempotent — re-running on a clean v0.7 project is a no-op.

    Exit codes:
      0 — success (migrated, or already-migrated no-op).
      1 — refused: user edits detected on disk; re-run with --force after
          relocating the content (use the guidance the command prints).
      2 — yaml unreadable or invalid (cannot parse ai-hats.yaml at all).
      3 — git stage/commit failure. Filesystem deletions and staging are
          intact; resolve the git error (e.g. set user.email) and either
          commit the staged tree manually or roll back with
          ``git restore --staged . && git checkout -- .``.
      4 — write_canonical failure after the sweep (rare). The deletions
          have already happened; recover with ``git checkout -- .`` and
          re-run.
    """
    import subprocess

    import yaml

    from ..migration_v07 import (
        check_branches_modify_paths,
        detect_yaml_changes,
        execute_deletions,
        plan_migration,
    )

    project_dir = _project_dir()
    config_path = project_dir / "ai-hats.yaml"
    if not config_path.exists():
        console.print(
            f"[red]migrate-v07: no ai-hats.yaml at {project_dir} — "
            "nothing to migrate[/]"
        )
        sys.exit(2)

    # Capture raw yaml shape so detect_yaml_changes can tell us what would
    # change on disk when we re-save. ``Assembler.__init__`` calls from_yaml
    # itself (with strip/heal); we let that be the single source of WARNs
    # so the user sees each diagnostic exactly once per invocation.
    try:
        raw = yaml.safe_load(config_path.read_text()) or {}
    except yaml.YAMLError as e:
        console.print(f"[red]migrate-v07: cannot parse {config_path}[/]: {e}")
        sys.exit(2)

    try:
        asm = _assembler(project_dir)
    except Exception as e:  # noqa: BLE001 — ProjectConfigError or pydantic surface
        console.print(f"[red]migrate-v07: invalid yaml[/]: {e}")
        sys.exit(2)
    cfg = asm.project_config

    yaml_changes = detect_yaml_changes(raw, cfg)
    canonical_dir = project_dir / cfg.ai_hats_dir

    effective_role = cfg.active_role or cfg.default_role
    if effective_role:
        try:
            composition = asm.composer.compose(
                effective_role, overlay=asm._get_overlay(effective_role)
            )
        except Exception:  # noqa: BLE001 — defensive: fall back to empty baseline
            logger.debug("composition failed; using empty baseline", exc_info=True)
            composition = _empty_composition()
        source_lookup = _build_tier2_source_lookup(asm)
    else:
        composition = _empty_composition()
        source_lookup = {}

    report = plan_migration(
        canonical_dir, composition, source_lookup, project_dir=project_dir
    )

    # --check-branches is additive: surface a warning, then continue to the
    # refuse/force decision. Never blocks on its own.
    if check_branches and report.paths_to_delete:
        warns = check_branches_modify_paths(project_dir, report.paths_to_delete)
        if warns:
            console.print(
                "[yellow]WARN[/] local branches modify paths "
                "that will be deleted:"
            )
            for branch, paths in warns:
                console.print(f"  [yellow]{branch}[/]")
                for p in paths:
                    console.print(f"    {p}")
            console.print(
                "  Merge / cherry-pick first or those edits will be lost."
            )

    # Refuse on user edits unless --force.
    if report.user_edits and not force:
        console.print(
            "[red]ai-hats: migrate-v07: refusing[/] — "
            "detected user edits at:"
        )
        console.print()
        for f in report.user_edits:
            try:
                rel = f.path.relative_to(project_dir)
            except ValueError:
                rel = f.path
            console.print(f"  [yellow]{rel}[/]")
            console.print(
                f"    → tier {f.tier} ({f.kind}). "
                + _migration_guidance(f.tier, f.kind, f.path.name)
            )
            console.print()
        console.print(
            "Use [bold]--force[/] to overwrite (with stderr WARN per file) "
            "after relocating content."
        )
        sys.exit(1)

    # Nothing on disk + no yaml changes → already migrated.
    if not report.has_work:
        console.print("[green]Already migrated[/] — nothing to do.")
        return

    # --force path: surface one WARN to stderr per file we are about to
    # overwrite, so the user has a paper trail even when stdout is captured.
    if force and report.user_edits:
        for f in report.user_edits:
            try:
                rel = f.path.relative_to(project_dir)
            except ValueError:
                rel = f.path
            print(
                f"WARN: migrate-v07: overwriting {rel} (user edit detected)",
                file=sys.stderr,
            )

    # Persist yaml hardening (deprecated-field strip + default_role heal).
    # cfg.to_dict already excludes the deprecated keys (they're not on the
    # model) and includes the healed default_role, so a fresh save normalises
    # the shape in one go.
    if yaml_changes:
        cfg.save(config_path)

    # Sweep stale files. Order matters: filesystem first, then imports.md
    # regeneration via write_canonical so the resulting MANAGED manifest
    # reflects post-sweep reality.
    removed = execute_deletions(report, canonical_dir)
    try:
        asm.write_canonical()
    except Exception as e:  # noqa: BLE001 — partial state is recoverable from git
        console.print(f"[red]migrate-v07: write_canonical failed[/]: {e}")
        sys.exit(4)

    # Stage everything (deletions included). Single commit envelope.
    rel_canonical = canonical_dir.relative_to(project_dir)
    add = subprocess.run(
        [
            "git", "-C", str(project_dir), "add", "-A", "--",
            str(rel_canonical), config_path.name,
        ],
        capture_output=True, text=True, check=False,
    )
    if add.returncode != 0:
        console.print(
            f"[red]migrate-v07: git add failed[/]: "
            f"{add.stderr.strip() or add.stdout.strip()}"
        )
        sys.exit(3)

    # Was anything actually staged? (Idempotency guard — if everything was
    # already clean, exit silently without an empty commit.)
    diff = subprocess.run(
        ["git", "-C", str(project_dir), "diff", "--cached", "--quiet"],
        check=False,
    )
    if diff.returncode == 0:
        console.print("[green]Already migrated[/] — nothing to commit.")
        return

    if no_commit:
        console.print(
            f"[green]Staged[/] migration changes "
            f"({len(removed)} on-disk removals + yaml normalisation)."
        )
        console.print(
            "Review with [bold]git diff --cached[/] before committing."
        )
        return

    commit = subprocess.run(
        ["git", "-C", str(project_dir), "commit", "-m", _MIGRATE_COMMIT_MESSAGE],
        capture_output=True, text=True, check=False,
    )
    if commit.returncode != 0:
        console.print(
            f"[red]migrate-v07: git commit failed[/]: "
            f"{commit.stderr.strip() or commit.stdout.strip()}"
        )
        console.print(
            "  Filesystem changes are intact and staged; resolve the git "
            "error (e.g. set user.email) and run "
            "[bold]git commit -F-[/] with the message from the docstring."
        )
        sys.exit(3)

    console.print(
        f"[green]Migrated[/] — {len(removed)} files/dirs removed, "
        "single atomic commit created."
    )
