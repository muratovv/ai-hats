"""Managed-directory location: .gitignore-entry management + relocation (HATS-715).

``ensure_gitignore_entry`` / ``_gitignore_swap_entry`` are pure over a project dir;
``relocate`` takes the :class:`Assembler` for project_config / config_path. Assembler
keeps thin delegators (``_ensure_gitignore_entry`` at init, public ``relocate``).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from .assembler import GITIGNORE_FILE
from .safe_delete import discard as _safe_discard
from .safe_delete import replace as _safe_replace

if TYPE_CHECKING:
    from .assembler import Assembler

# Top-level entries under <ai_hats_dir>/ that relocate() moves to the new
# location. Order: directories first (cheap renames), files last. `.venv` is
# intentionally NOT here — managed venvs are deleted (internal absolute paths
# break on move) and recreated on next session.
_RELOCATE_ENTRIES: tuple[str, ...] = (
    "library",
    "tracker",
    "sessions",
    "traces",
    "pipeline_steps",
    "STATE.md",
    ".last_backup",
)


class RelocationResult:
    """Outcome of :meth:`Assembler.relocate`. Diagnostic-only; CLI prints it."""

    __slots__ = ("old", "new", "changed", "moved", "venv_removed", "gitignore_updated")

    def __init__(
        self,
        *,
        old: str,
        new: str,
        changed: bool,
        moved: list[str] | None = None,
        venv_removed: bool = False,
        gitignore_updated: bool = False,
    ) -> None:
        self.old = old
        self.new = new
        self.changed = changed
        self.moved = moved or []
        self.venv_removed = venv_removed
        self.gitignore_updated = gitignore_updated


def ensure_gitignore_entry(project_dir: Path) -> None:
    """One-shot: ensure `.agent/ai-hats/` (or current `<ai_hats_dir>/`) is in .gitignore.

    HATS-317 removed the dynamic managed-block generator. The new policy
    is a single static line written once at ``init`` time. ``set_role``
    and ``bump`` do not touch .gitignore — the user owns the file.
    Idempotent: re-running ``init`` is a no-op if the line is present.
    """
    from .paths import _read_ai_hats_dir_from_yaml

    gitignore = project_dir / GITIGNORE_FILE
    ai_hats_rel = _read_ai_hats_dir_from_yaml(project_dir) or ".agent/ai-hats"
    # Normalize: trailing slash so directories are matched explicitly.
    line = ai_hats_rel.rstrip("/") + "/"

    if not gitignore.exists():
        _safe_replace(
            gitignore,
            (line + "\n").encode("utf-8"),
            reason="gitignore-init",
            project_dir=project_dir,
        )
        return
    existing = gitignore.read_text()
    existing_lines = {ln.strip() for ln in existing.splitlines()}
    if line in existing_lines:
        return
    sep = "" if existing.endswith("\n") else "\n"
    _safe_replace(
        gitignore,
        (existing + sep + line + "\n").encode("utf-8"),
        reason="gitignore-append",
        project_dir=project_dir,
    )


def _gitignore_swap_entry(project_dir: Path, old_rel: str, new_rel: str) -> bool:
    """Replace .gitignore line `old_rel/` with `new_rel/`.

    Returns True if the file was changed. Idempotent: if the old line is
    missing, just ensures the new line is present. No-op when both lines
    already match the desired post-state.
    """
    gitignore = project_dir / GITIGNORE_FILE
    old_line = old_rel.rstrip("/") + "/"
    new_line = new_rel.rstrip("/") + "/"

    if not gitignore.exists():
        _safe_replace(
            gitignore,
            (new_line + "\n").encode("utf-8"),
            reason="gitignore-swap",
            project_dir=project_dir,
        )
        return True

    text = gitignore.read_text()
    lines = text.splitlines()
    seen_new = any(ln.strip() == new_line for ln in lines)
    out: list[str] = []
    swapped = False
    for ln in lines:
        stripped = ln.strip()
        if stripped == old_line:
            if not seen_new and not swapped:
                out.append(new_line)
                swapped = True
            # else: drop duplicate old entry
            continue
        out.append(ln)
    if not swapped and not seen_new:
        out.append(new_line)
    body = "\n".join(out)
    if text.endswith("\n"):
        body += "\n"
    if body == text:
        return False
    _safe_replace(
        gitignore,
        body.encode("utf-8"),
        reason="gitignore-swap",
        project_dir=project_dir,
    )
    return True


def relocate(a: "Assembler", new_dir: str) -> "RelocationResult":
    """Move framework directory from current ``ai_hats_dir`` to ``new_dir``.

    Steps (idempotent — partial-failure re-run completes what's missing):
      1. Validate ``new_dir`` via :func:`normalize_ai_hats_dir`.
      2. Move ``library / tracker / sessions / traces / pipeline_steps /
         STATE.md / .last_backup`` to the new location.
      3. If venv is managed (``venv_path is None``) and ``<old>/.venv``
         exists — delete it. The bash launcher recreates the venv on
         next session at the new location.
      4. Persist ``ai_hats_dir = new_dir`` in ``ai-hats.yaml``.
      5. If ``manage_gitignore=true`` — swap the old/ entry for new/.
      6. Remove ``<old>/`` if empty.

    Raises:
        ValueError: ``new_dir`` invalid OR destination already exists and
          contains conflicting entries.
    """
    from .paths import normalize_ai_hats_dir

    new_rel = normalize_ai_hats_dir(new_dir)
    old_rel = a.project_config.ai_hats_dir
    if old_rel == new_rel:
        return RelocationResult(old=old_rel, new=new_rel, changed=False)

    old_abs = a.project_dir / old_rel
    new_abs = a.project_dir / new_rel

    # Refuse if destination has any entry that would collide with what
    # we're about to move. An EMPTY destination (or one containing only
    # leftovers from a partial previous run) is fine.
    if new_abs.exists():
        for name in _RELOCATE_ENTRIES:
            src = old_abs / name
            dst = new_abs / name
            if src.exists() and dst.exists():
                raise ValueError(
                    f"relocate: destination collision at {new_rel}/{name} "
                    "— refusing to overwrite. Remove the existing entry "
                    "or pick a different ai_hats_dir."
                )

    new_abs.mkdir(parents=True, exist_ok=True)

    moved: list[str] = []
    for name in _RELOCATE_ENTRIES:
        src = old_abs / name
        dst = new_abs / name
        if not src.exists():
            continue
        if dst.exists():
            # Idempotent: previous run already moved this entry.
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        moved.append(name)

    venv_removed = False
    if a.project_config.venv_path is None:
        old_venv = old_abs / ".venv"
        if old_venv.exists():
            _safe_discard(
                old_venv,
                reason="venv-relocate",
                project_dir=a.project_dir,
            )
            venv_removed = True

    a.project_config.ai_hats_dir = new_rel
    a.project_config.save(a.config_path)

    gitignore_updated = False
    if a.project_config.manage_gitignore:
        gitignore_updated = _gitignore_swap_entry(a.project_dir, old_rel, new_rel)

    # Best-effort cleanup of an empty old dir. Leave it alone if the
    # user has unrelated files there.
    if old_abs.exists() and old_abs.is_dir():
        try:
            old_abs.rmdir()  # safe-delete: ok empty-dir
        except OSError:
            pass

    return RelocationResult(
        old=old_rel,
        new=new_rel,
        changed=True,
        moved=moved,
        venv_removed=venv_removed,
        gitignore_updated=gitignore_updated,
    )
