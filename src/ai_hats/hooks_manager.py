"""Managed-hook materialization (HATS-837 extract from Assembler, updated HATS-1480).

Owns skill-declared git hooks (``.githooks/``). Worktree hooks spawn in place
from their declaring skills (HATS-1269); runtime hooks live in the session tree
(HATS-1268).

Narrow DI: ``project_dir`` + a live ``project_config`` reference + a
``resolve_provider`` callable.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from ai_hats_core import CompositionResult, scrubbed_git_env
from ai_hats_core.safe_delete import discard as _safe_discard
from .hook_collection import resolve_skill_script as _resolve_runtime_script
from .models import SkillMetadata
from . import owners


def _read_manifest(path: Path) -> set[str]:
    """Managed names recorded in a ``.manifest`` — plain or hashed format (HATS-911)."""
    from .sweeper import read_marker_names

    return read_marker_names(path)


if TYPE_CHECKING:
    from .models import ProjectConfig
    from .surfaces import Surface

logger = logging.getLogger(__name__)

# Retiring this mechanism = dropping this line; the unclaimed-marker
# sweeper then reclaims marker-listed .githooks/ artifacts on next init/bump.
owners.register_owner("git-hooks", module=__name__)


class HookError(Exception):
    """Unrecoverable managed-hook materialization fault.

    Hook-local so this low-level module never imports from the higher-level
    ``assembler`` package (which imports *us*, not the reverse).
    """


class HooksManager:
    """Materialize managed-hook surfaces (HATS-837, HATS-1480).

    See module docstring for the narrow-DI contract.
    """

    def __init__(
        self,
        project_dir: Path,
        project_config: "ProjectConfig",
        *,
        resolve_provider: "Callable[[str], Surface]",
    ) -> None:
        self.project_dir = project_dir
        self.project_config = project_config
        self.resolve_provider = resolve_provider

    def materialize(
        self, result: "CompositionResult | None", *, warnings_sink: list[str] | None = None
    ) -> None:
        """Bring managed-hook surfaces on disk in sync with ``result``."""
        if self.binary_behind_source():
            if warnings_sink is not None:
                warnings_sink.append(
                    "installed ai-hats is behind upstream — skipping hook materialization (run 'ai-hats self update')"
                )
            return

        if result is not None and (self.project_dir / ".git").exists():
            self.install_git_hooks(result, warnings_sink=warnings_sink)

    def install_git_hooks(
        self, result: CompositionResult, *, warnings_sink: list[str] | None = None
    ) -> None:
        """Install skill-declared git hooks (mechanics are the module functions below)."""
        install_git_hooks(self.project_dir, result, warnings_sink=warnings_sink)

    def binary_behind_source(self) -> bool:
        """True if the installed ai-hats binary is strictly behind upstream."""
        try:
            from .update_check import upstream_update

            return upstream_update(self.project_dir) is not None
        except Exception:  # silent-ok: unknown means "not behind" — see docstring
            return False


# ----- git-hook mechanics (HATS-837: merged from the former githooks.py) -----
# Pure functions over (project_dir, CompositionResult); the HooksManager methods
# above are the OOP seam onto them.

GITHOOKS_DIR = ".githooks"
GITHOOKS_MANIFEST = ".ai-hats-manifest"
# Takeover records the displaced hooks dir here; the dispatcher
# chains to it live so the repo's own hook manager keeps running.
PREVIOUS_HOOKS_PATH_KEY = "ai-hats.previousHooksPath"
GITHOOKS_DISPATCHER_MARKER = "AI-HATS-DISPATCHER-MARKER"
GITHOOKS_DISPATCHER_TEMPLATE = Path(__file__).parent / "templates" / "githooks" / "dispatcher.sh"
#: Lives in `.githooks/`, not `<event>.d/` — the dispatcher executes everything
#: in `<event>.d/`, and this file is sourced, not run.
GITHOOKS_BYPASS_JOURNAL = "bypass_journal.sh"


def install_git_hooks(
    project_dir: Path, result: CompositionResult, *, warnings_sink: list[str] | None = None
) -> None:
    """Install the git orchestrator: one dispatcher per declared event.

    The dispatcher is the ONLY durable artifact (ADR-0020 D3) — no gate copies,
    no manifest. Gate *content* stays in the declaring skills and is resolved at
    commit time (``ai-hats githooks resolve``), which is what lets a
    ``self update`` take effect with no re-materialization step (M12).

    Conflict policy:
    - `.githooks/<event>` exists WITHOUT our marker → leave alone, warn.
    - `core.hooksPath` pre-set elsewhere → take over, recording the displaced
      dir for dispatcher chaining, and announce loudly (HATS-999).
    """
    warnings: list[str] = []
    _retire_pre_1337_layout(project_dir)

    declared = _collect_skill_git_hooks(result)
    wanted = {event for event, entries in declared.items() if entries}
    # A composition that LOST content cannot say a gate is absent: "declares no
    # hooks" and "the skill that declared them dropped out" reach here as the
    # same empty dict, and dropping on the second uninstalls the repo's gates
    # over a typo. Installing what IS wanted stays unconditional — additive.
    if result.lost:
        losses = "; ".join(str(e) for e in result.lost)
        warnings.append(
            f"git_hooks: role {result.name!r} composed with losses ({losses}) — "
            "existing dispatchers left in place, because a lost gate is "
            "indistinguishable from no gate here."
        )
    else:
        _drop_unwanted_dispatchers(project_dir, wanted)
    if not wanted:
        _flush(warnings, warnings_sink)
        return

    githooks_dir = project_dir / GITHOOKS_DIR
    githooks_dir.mkdir(exist_ok=True)

    for event in sorted(wanted):
        dispatcher_path = githooks_dir / event
        if not _install_dispatcher(dispatcher_path):
            warnings.append(
                f"git_hooks: existing {dispatcher_path} is not managed by "
                f"ai-hats — left in place. Gates for '{event}' will not run "
                f"unless you wire the ai-hats dispatcher into it manually."
            )

    _configure_hooks_path(project_dir, warnings)

    _flush(warnings, warnings_sink)


def _flush(warnings: list[str], warnings_sink: list[str] | None) -> None:
    if warnings_sink is not None:
        warnings_sink.extend(warnings)
        return
    for w in warnings:
        print(f"[ai-hats] WARNING: {w}")


def _managed_dispatchers(githooks_dir: Path) -> list[Path]:
    """Every `.githooks/<event>` that proves, by content, it is ours.

    Ownership is proven by the marker the dispatcher carries, never by a list of
    names: the manifest that used to hold those names is gone (HATS-1337), and a
    hand-written foreign hook of the same name must stay untouched.
    """
    if not githooks_dir.is_dir():
        return []
    found = []
    for child in sorted(githooks_dir.iterdir()):
        if not child.is_file():
            continue
        try:
            if GITHOOKS_DISPATCHER_MARKER in child.read_text():
                found.append(child)
        except (OSError, UnicodeDecodeError):
            continue
    return found


def _drop_unwanted_dispatchers(project_dir: Path, wanted: set[str]) -> None:
    """Remove our dispatchers for events no skill declares any more."""
    githooks_dir = project_dir / GITHOOKS_DIR
    for dispatcher in _managed_dispatchers(githooks_dir):
        if dispatcher.name not in wanted:
            _safe_discard(dispatcher, reason="githook-dispatcher", project_dir=project_dir)


def _retire_pre_1337_layout(project_dir: Path) -> None:
    """One-time removal of the gate copies and the manifest that listed them.

    Cleanup is driven BY the old manifest, which is exactly what distinguishes
    an ai-hats copy from a script the user dropped into `<event>.d/` themselves —
    that affordance survives (R8). Once the manifest is gone there is nothing
    left to drive, so this is self-terminating rather than a standing sweep.
    """
    githooks_dir = project_dir / GITHOOKS_DIR
    manifest_path = githooks_dir / GITHOOKS_MANIFEST
    if not manifest_path.exists():
        return
    try:
        entries = _read_manifest(manifest_path)
    except OSError:
        entries = []
    for entry in sorted(entries):
        # Only the copies. Dispatchers are bare names and stay — they are the
        # artifact we keep; `_install_dispatcher` refreshes them in place.
        if "/" not in entry:
            continue
        target = githooks_dir / entry
        if target.is_file():
            _safe_discard(target, reason="githook-retired-copy", project_dir=project_dir)
    # The retired bypass-journal copy: a bare name, but not a dispatcher.
    journal_copy = githooks_dir / GITHOOKS_BYPASS_JOURNAL
    if journal_copy.is_file():
        _safe_discard(journal_copy, reason="githook-retired-copy", project_dir=project_dir)
    manifest_path.unlink(missing_ok=True)  # safe-delete: ok framework-manifest
    for child in list(githooks_dir.iterdir()):
        if child.is_dir() and child.name.endswith(".d") and not any(child.iterdir()):
            child.rmdir()  # safe-delete: ok empty-dir


def _collect_skill_git_hooks(
    result: CompositionResult,
) -> dict[str, list[tuple[str, str]]]:
    """Walk composed skills and collect their declared git hooks.

    Returns: {event_name: [(skill_name, script_path), ...]}
    """
    collected: dict[str, list[tuple[str, str]]] = {}
    for skill in result.skills:
        metadata = SkillMetadata.from_skill_dir(skill.source_path)
        if not metadata.git_hooks:
            continue
        for event, scripts in metadata.git_hooks.items():
            collected.setdefault(event, []).extend((skill.name, script) for script in scripts)
    return collected


def _resolve_skill_script(
    skill_name: str,
    script_path: str,
    result: CompositionResult,
) -> Path | None:
    """Resolve a script path declared in a skill's metadata to an absolute path."""
    return _resolve_runtime_script(result, skill_name, script_path)


def _install_dispatcher(dispatcher_path: Path) -> bool:
    """Write the dispatcher script. Returns True if installed/updated, False on conflict."""
    if dispatcher_path.exists():
        try:
            existing = dispatcher_path.read_text()
        except OSError:
            return False
        if GITHOOKS_DISPATCHER_MARKER not in existing:
            return False  # Foreign file, leave it alone.
    if not GITHOOKS_DISPATCHER_TEMPLATE.exists():
        # Should never happen with package-data set, but defend against it.
        return False
    shutil.copy2(GITHOOKS_DISPATCHER_TEMPLATE, dispatcher_path)
    dispatcher_path.chmod(0o755)
    return True


def _same_hooks_path(a: str, b: str, project_dir: Path) -> bool:
    """True when two core.hooksPath values resolve to the same dir (HATS-969).

    A relative value is taken against ``project_dir`` (git's working-tree root),
    mirroring how git interprets a relative ``core.hooksPath``."""

    def _norm(value: str) -> Path:
        p = Path(value).expanduser()
        return (p if p.is_absolute() else project_dir / p).resolve()

    return _norm(a) == _norm(b)


def _configure_hooks_path(project_dir: Path, warnings: list[str]) -> None:
    """Set git config core.hooksPath = .githooks if safe to do so."""
    try:
        current = subprocess.run(
            ["git", "config", "--get", "core.hooksPath"],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            check=False,
            env=scrubbed_git_env(),
        )
    except (OSError, FileNotFoundError):
        warnings.append("git not found — cannot configure core.hooksPath")
        return

    existing = current.stdout.strip() if current.returncode == 0 else ""
    # ABSOLUTE, not `.githooks`. Git resolves a relative hooksPath
    # against the working tree it is invoked in, and `.githooks/` is generated +
    # gitignored, so it never exists in a linked worktree — every gate was
    # silently off in every worktree. Worktrees share `.git/config`, so one
    # absolute value gates all of them.
    target = str((project_dir / GITHOOKS_DIR).resolve())

    if existing == target:
        return  # Already exact.

    # Same directory, different spelling — the pre-1337 relative value. Rewrite
    # it in place: NOT a takeover, so no previousHooksPath record (that would
    # self-point) and no warning about hooks that never moved.
    if existing and _same_hooks_path(existing, target, project_dir):
        try:
            subprocess.run(
                ["git", "config", "core.hooksPath", target],
                cwd=str(project_dir),
                capture_output=True,
                text=True,
                check=True,
                env=scrubbed_git_env(),
            )
        except subprocess.CalledProcessError as e:
            warnings.append(f"failed to set core.hooksPath: {e.stderr.strip() or e}")
        return

    try:
        if existing:
            # Record the displaced dir so the dispatcher chains to it.
            subprocess.run(
                ["git", "config", PREVIOUS_HOOKS_PATH_KEY, existing],
                cwd=str(project_dir),
                capture_output=True,
                text=True,
                check=True,
                env=scrubbed_git_env(),
            )
        subprocess.run(
            ["git", "config", "core.hooksPath", target],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            check=True,
            env=scrubbed_git_env(),
        )
    except subprocess.CalledProcessError as e:
        warnings.append(f"failed to set core.hooksPath: {e.stderr.strip() or e}")
        return

    if existing:
        warnings.append(
            f"core.hooksPath: '{existing}' → '{target}' (taken over, HATS-999). "
            f"Previous hooks keep running: the dispatcher chains to "
            f"'{existing}/<event>' after ai-hats hooks. "
            f"Revert: git config core.hooksPath {existing}"
        )
