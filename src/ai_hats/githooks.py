"""Git-hook installation, drift detection, and sync mechanics (HATS-715 extract).

Pure functions over a ``project_dir`` + composed ``CompositionResult``; the
orchestration (compose, version-skew check) stays on :class:`Assembler` as
``sync_hooks`` / thin ``_install_git_hooks`` / ``_git_hooks_drift`` delegators.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .composer import CompositionResult
from .composer import resolve_skill_script as _resolve_runtime_script
from .models import SkillMetadata
from .safe_delete import discard as _safe_discard

GITHOOKS_DIR = ".githooks"
GITHOOKS_MANIFEST = ".ai-hats-manifest"
GITHOOKS_DISPATCHER_MARKER = "AI-HATS-DISPATCHER-MARKER"
GITHOOKS_DISPATCHER_TEMPLATE = Path(__file__).parent / "templates" / "githooks" / "dispatcher.sh"


def install_git_hooks(project_dir: Path, result: CompositionResult) -> None:
    """Install git hooks declared by composed skills.

    Skills declare hooks in their `metadata.yaml` under `git_hooks:`.
    Each declared script is copied into `.githooks/<event>.d/<skill>-<basename>`,
    a dispatcher script is generated at `.githooks/<event>`, and
    `core.hooksPath` is set to `.githooks` (idempotently).

    Conflict policy:
    - If `.githooks/<event>` exists WITHOUT our marker → leave alone, warn.
    - If `core.hooksPath` is set to a non-`.githooks` value → leave alone, warn.
    - Files inside `<event>.d/` from previous installs are tracked via a
      manifest at `.githooks/.ai-hats-manifest` and removed before re-install,
      so stale hooks from removed skills don't linger.
    """
    declared = _collect_skill_git_hooks(result)
    if not declared:
        # No skill declares git hooks. Don't touch user's repo.
        # Still clean up our previously-installed managed files (in case the
        # user removed all skills with git_hooks) so stale entries don't linger.
        _cleanup_managed_git_hooks(project_dir)
        return

    githooks_dir = project_dir / GITHOOKS_DIR
    githooks_dir.mkdir(exist_ok=True)

    # Remove anything we previously owned, then re-install fresh.
    _cleanup_managed_git_hooks(project_dir)

    new_manifest: list[str] = []
    warnings: list[str] = []

    for event, entries in declared.items():
        if not entries:
            continue
        event_d = githooks_dir / f"{event}.d"
        event_d.mkdir(exist_ok=True)

        for skill_name, script_path in entries:
            src = _resolve_skill_script(skill_name, script_path, result)
            if src is None:
                warnings.append(
                    f"git_hooks: skill '{skill_name}' declares script "
                    f"'{script_path}' but file not found"
                )
                continue
            dest_basename = f"{skill_name}-{src.name}"
            dest = event_d / dest_basename
            shutil.copy2(src, dest)
            dest.chmod(0o755)
            new_manifest.append(f"{event}.d/{dest_basename}")

        # Generate dispatcher (or warn on conflict).
        dispatcher_path = githooks_dir / event
        installed = _install_dispatcher(dispatcher_path)
        if installed:
            new_manifest.append(event)
        else:
            warnings.append(
                f"git_hooks: existing {dispatcher_path} is not managed by "
                f"ai-hats — left in place. Hooks for '{event}' will not run "
                f"unless you wire {event}.d/* into it manually."
            )

    # Persist manifest of files we own.
    manifest_path = githooks_dir / GITHOOKS_MANIFEST
    manifest_path.write_text("\n".join(new_manifest) + "\n")

    # Configure core.hooksPath (idempotent + safe).
    _configure_hooks_path(project_dir, warnings)

    for w in warnings:
        print(f"[ai-hats] WARNING: {w}")


def _collect_skill_git_hooks(
    result: CompositionResult,
) -> dict[str, list[tuple[str, str]]]:
    """Walk composed skills and collect their declared git hooks.

    Returns: {event_name: [(skill_name, script_path), ...]}
    """
    collected: dict[str, list[tuple[str, str]]] = {}
    for skill in result.skills:
        metadata_path = skill.source_path / "metadata.yaml"
        metadata = SkillMetadata.from_yaml(metadata_path)
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


def _cleanup_managed_git_hooks(project_dir: Path) -> None:
    """Remove files listed in our manifest. Idempotent."""
    githooks_dir = project_dir / GITHOOKS_DIR
    manifest_path = githooks_dir / GITHOOKS_MANIFEST
    if not manifest_path.exists():
        return
    try:
        entries = manifest_path.read_text().splitlines()
    except OSError:
        return
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        target = githooks_dir / entry
        if target.is_file():
            # For dispatcher files, only remove if the marker is still ours.
            if "/" not in entry:
                try:
                    if GITHOOKS_DISPATCHER_MARKER not in target.read_text():
                        continue
                except OSError:
                    continue
            _safe_discard(
                target,
                reason="githook-dispatcher",
                project_dir=project_dir,
            )
    # Manifest itself is framework bookkeeping — whitelist.
    manifest_path.unlink(missing_ok=True)  # safe-delete: ok framework-manifest
    # Remove empty <event>.d/ subdirs.
    for child in githooks_dir.iterdir():
        if child.is_dir() and child.name.endswith(".d") and not any(child.iterdir()):
            child.rmdir()  # safe-delete: ok empty-dir


def _configure_hooks_path(project_dir: Path, warnings: list[str]) -> None:
    """Set git config core.hooksPath = .githooks if safe to do so."""
    try:
        current = subprocess.run(
            ["git", "config", "--get", "core.hooksPath"],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, FileNotFoundError):
        warnings.append("git not found — cannot configure core.hooksPath")
        return

    existing = current.stdout.strip() if current.returncode == 0 else ""
    target = GITHOOKS_DIR

    if existing == target:
        return  # Already correct.
    if existing:
        warnings.append(
            f"core.hooksPath is already set to '{existing}' — not "
            f"overwriting. To enable ai-hats hooks, run: "
            f"git config core.hooksPath {target}  (or merge dispatchers manually)"
        )
        return

    try:
        subprocess.run(
            ["git", "config", "core.hooksPath", target],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        warnings.append(f"failed to set core.hooksPath: {e.stderr.strip() or e}")


def expected_git_hook_files(project_dir: Path, result: CompositionResult) -> dict[str, bytes]:
    """Managed ``.githooks/`` relpath -> expected bytes for ``result``.

    Mirrors :meth:`_install_git_hooks` WITHOUT writing, so drift can be
    detected cheaply. Keys: ``<event>.d/<skill>-<basename>`` per declared
    script, plus ``<event>`` per dispatcher.
    """
    declared = _collect_skill_git_hooks(result)
    expected: dict[str, bytes] = {}
    for event, entries in declared.items():
        has_entry = False
        for skill_name, script_path in entries:
            src = _resolve_skill_script(skill_name, script_path, result)
            if src is None:
                continue
            expected[f"{event}.d/{skill_name}-{src.name}"] = src.read_bytes()
            has_entry = True
        if has_entry and GITHOOKS_DISPATCHER_TEMPLATE.exists():
            expected[event] = GITHOOKS_DISPATCHER_TEMPLATE.read_bytes()
    return expected


def git_hooks_drift(project_dir: Path, result: CompositionResult, manifest_set: set[str]) -> bool:
    """True if managed git hooks on disk diverge from ``result``.

    Compares the expected managed file set (content + exec-bit + presence)
    and the manifest against disk. A foreign (non-marker) dispatcher is
    left to the install policy and is never counted as drift here.
    """
    githooks_dir = project_dir / GITHOOKS_DIR
    expected = expected_git_hook_files(project_dir, result)
    if not expected:
        # Nothing should be installed → drift iff a stale manifest lingers.
        return manifest_set != set()
    if manifest_set != set(expected):
        return True
    for rel, content in expected.items():
        target = githooks_dir / rel
        if not target.is_file():
            return True
        # Top-level dispatcher with a foreign body → leave alone (not drift).
        if "/" not in rel:
            try:
                if GITHOOKS_DISPATCHER_MARKER not in target.read_text():
                    continue
            except OSError:
                return True
        try:
            if target.read_bytes() != content:
                return True
        except OSError:
            return True
        if not target.stat().st_mode & 0o100:  # owner-exec bit lost
            return True
    return False
