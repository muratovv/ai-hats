"""Module-level safe-delete API: trash bin for destructive ops.

Single point of truth for destructive filesystem operations in ai-hats
core. Replaces raw ``path.unlink()``, ``shutil.rmtree()``, and in-place
``path.write_text(new)`` calls with :func:`discard` / :func:`replace`
helpers that snapshot victim content to a per-process trash session
under ``${TMPDIR}/ai-hats/trash-<utc-ts>-<pid>/``.

Recovery: ``cp -r <session>/<rel> <project>/<rel>``. Sessions are NOT
auto-cleaned — OS ``/tmp`` cleanup handles retention.

Configuration via env:

* ``AI_HATS_TRASH_DIR=<path>`` — override base dir (session still gets a
  ``trash-<ts>-<pid>/`` subdir underneath).
* ``AI_HATS_TRASH_DIR=-`` — hard-delete mode (no snapshots, WARN per op).
  For CI / ephemeral environments where snapshot value is zero.

Failures:

* ENOSPC / read-only fs on session create or write → :class:`TrashFullError`
  propagated to caller. Callers (bump / init) abort hard rather than
  silently lose recovery capability.

See ``tracker/backlog/tasks/HATS-470/plan.md`` for full design.
"""
from __future__ import annotations

import errno
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

__all__ = [
    "discard",
    "replace",
    "session_summary",
    "session_root",
    "reset_session",
    "TrashFullError",
    "ENV_TRASH_DIR",
    "HARD_DELETE_SENTINEL",
]


ENV_TRASH_DIR = "AI_HATS_TRASH_DIR"
HARD_DELETE_SENTINEL = "-"
SESSION_PREFIX = "trash-"
MANIFEST_NAME = "MANIFEST.md"
NAMESPACE = "ai-hats"


class TrashFullError(OSError):
    """Raised when trash cannot accept the move (ENOSPC, read-only fs, etc).

    Callers MUST treat this as fatal — partial migrations without a
    recoverable snapshot violate the trash-bin contract.
    """


@dataclass
class _Entry:
    """One recorded destructive op for the session manifest."""

    ts: str
    op: str  # "discard" | "replace" | "clean-tmp" | "hard-rm" | "hard-replace"
    reason: str
    target: str  # human-readable "<orig>" or "<orig> -> <trash>"


@dataclass
class _Session:
    """One per-process trash session. Created lazily on first op."""

    root: Path
    hard_delete: bool
    entries: list[_Entry] = field(default_factory=list)


# Module state. Lazy: nothing on disk until first discard/replace.
_session_lock = Lock()
_current_session: _Session | None = None


# ---------------------- Configuration ----------------------


def _resolve_base() -> tuple[Path | None, bool]:
    """Return ``(base_dir, hard_delete)``. ``base_dir`` is None when ``hard_delete=True``."""
    env = os.environ.get(ENV_TRASH_DIR, "").strip()
    if env == HARD_DELETE_SENTINEL:
        return None, True
    if env:
        return Path(env), False
    return Path(tempfile.gettempdir()) / NAMESPACE, False


def _ensure_session() -> _Session:
    """Lazy-create the per-process session. Idempotent within a process."""
    global _current_session
    with _session_lock:
        if _current_session is not None:
            return _current_session
        base, hard = _resolve_base()
        if hard:
            # Sentinel path — never written to. Hard-delete branches in
            # discard/replace short-circuit before any IO uses this.
            _current_session = _Session(
                root=Path("/dev/null"), hard_delete=True
            )
            return _current_session
        assert base is not None  # narrow the type for mypy
        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        # mkdtemp guarantees uniqueness even when two ai-hats processes
        # (or two pytest cases within the same wall second) initialise
        # sessions back-to-back — its random suffix avoids collisions
        # that a naive ts+pid path triggered. ts + pid still encoded
        # in the prefix for human grep-ability.
        try:
            base.mkdir(parents=True, exist_ok=True)
            session_str = tempfile.mkdtemp(
                prefix=f"{SESSION_PREFIX}{ts}-{os.getpid()}-",
                dir=str(base),
            )
        except OSError as e:
            if e.errno == errno.ENOSPC:
                raise TrashFullError(
                    f"Cannot create trash session under {base}: no space left. "
                    f"Free up {base} or set {ENV_TRASH_DIR}=- to disable trash."
                ) from e
            if e.errno in (errno.EACCES, errno.EROFS):
                raise TrashFullError(
                    f"Cannot create trash session under {base}: "
                    f"permission denied or read-only filesystem ({e.strerror}). "
                    f"Set {ENV_TRASH_DIR} to a writable path or use "
                    f"{ENV_TRASH_DIR}=- to disable trash."
                ) from e
            raise
        _current_session = _Session(root=Path(session_str), hard_delete=False)
        return _current_session


# ---------------------- Path helpers ----------------------


# Well-known ai-hats tmp-artefact prefixes that survive across runs and
# should be hard-deleted (not moved into a trash session — they'd just
# pile up). HATS-407: ``ai-hats-backup-*`` are left by the retired
# ``_backup()`` helper. HATS-470: ``ai-hats-trash-*`` are prior trash
# sessions from earlier ai-hats runs.
_TMP_ARTEFACT_PREFIXES: tuple[str, ...] = (
    "ai-hats-backup-",
    "ai-hats-trash-",
)


def _is_under_tmp(path: Path) -> bool:
    """True if ``path`` should be hard-deleted instead of moved to trash.

    Two cases:

    1. **Under current trash session** — avoid трэш-в-трэш recursion if
       caller passes a path we just moved.
    2. **Well-known ai-hats tmp artefact** — first path component under
       ``$TMPDIR`` starts with ``ai-hats-backup-`` or ``ai-hats-trash-``.
       Moving these into a fresh trash session would just accumulate
       garbage with no recovery value.

    Note: a project that happens to live under ``$TMPDIR`` (test
    fixtures, dev sandboxes) is NOT caught by this — only paths with
    the explicit ai-hats prefix. Uses :meth:`Path.resolve` on both
    sides so macOS ``/var`` → ``/private/var`` symlinks don't defeat
    the prefix match.
    """
    try:
        abs_path = path.resolve()
    except OSError:
        return False

    # Case 1: under current trash session.
    if _current_session is not None and not _current_session.hard_delete:
        try:
            abs_path.relative_to(_current_session.root.resolve())
            return True
        except (OSError, ValueError):
            pass

    # Case 2: well-known ai-hats tmp artefact prefix.
    try:
        tmp_root = Path(tempfile.gettempdir()).resolve()
        rel = abs_path.relative_to(tmp_root)
    except (OSError, ValueError):
        return False
    if not rel.parts:
        return False
    first = rel.parts[0]
    return any(first.startswith(p) for p in _TMP_ARTEFACT_PREFIXES)


def _resolve_dest(
    path: Path, project_dir: Path | None, session: _Session
) -> Path:
    """Compute trash destination preserving project-relative structure.

    For paths inside ``project_dir`` → ``<session>/<relpath>``.
    For external paths (or ``project_dir is None``) →
    ``<session>/_external/<abs-tail-without-leading-slash>``.

    HATS-470 review A1: if the natural dest already exists (file, dir,
    or sibling ``.symlink`` sidecar from a prior symlink-discard), append
    a monotonic ``.1``, ``.2``, ... counter to the basename so a second
    op on the same victim in one session does NOT clobber the first
    snapshot. The actual returned path is collision-free at call time.
    """
    abs_path = path.absolute()
    if project_dir is not None:
        try:
            rel = abs_path.relative_to(project_dir.absolute())
            natural = session.root / rel
            return _disambiguate_dest(natural)
        except ValueError:
            pass
    # External: strip leading anchor (/) and nest under _external/.
    if abs_path.is_absolute():
        tail = Path(*abs_path.parts[1:])
    else:
        tail = abs_path
    return _disambiguate_dest(session.root / "_external" / tail)


def _disambiguate_dest(natural: Path) -> Path:
    """Return ``natural`` if free, else append ``.1``, ``.2``, ... .

    Considers both the path itself AND the sibling ``<name>.symlink``
    sidecar so that two symlink-discards on the same path don't lose
    the first sidecar.

    Race window between disambiguation and write is acceptable: the
    trash session is per-process and ai-hats has no concurrent
    destructive callers within one process.
    """
    if not natural.exists() and not natural.is_symlink() \
            and not (natural.parent / f"{natural.name}.symlink").exists():
        return natural
    counter = 1
    while True:
        candidate = natural.with_name(f"{natural.name}.{counter}")
        sidecar = candidate.parent / f"{candidate.name}.symlink"
        if not candidate.exists() and not candidate.is_symlink() \
                and not sidecar.exists():
            return candidate
        counter += 1


# ---------------------- IO primitives ----------------------


def _hard_delete(path: Path) -> None:
    """Best-effort hard delete. Symlinks unlinked (link only, target preserved).

    Internal helper — only invoked when trash is intentionally skipped
    (hard-delete env, under-tmp shortcut). Whitelisted from lint via
    the safe_delete.py exclusion in scripts/lint_no_raw_destructive.sh.
    """
    if path.is_symlink():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _move_to_trash(src: Path, dest: Path) -> None:
    """Move ``src`` to ``dest`` creating parent dirs. Translates ENOSPC.

    Symlinks: link itself unlinked, target preserved. Sidecar file
    ``<dest>.symlink`` records the original target string so the user
    can reconstruct the link if needed.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        if src.is_symlink():
            target = os.readlink(src)
            sidecar = dest.parent / f"{dest.name}.symlink"
            sidecar.write_text(target)
            src.unlink()
            return
        shutil.move(str(src), str(dest))
    except OSError as e:
        if e.errno == errno.ENOSPC:
            raise TrashFullError(
                f"Cannot move {src} to trash {dest}: no space left."
            ) from e
        raise


def _write_atomic(path: Path, content: bytes, mode: int | None = None) -> None:
    """Atomic write: tmp + rename. Internal helper (NOT routed through trash).

    Intentionally NOT delegated to ``ai_hats_core.atomic_io`` (HATS-716): ``safe_delete``
    is a designated leaf module (``test_import_hygiene.LEAF_MODULES``) that must
    import nothing first-party, so it keeps its own copy of the tmp+replace
    primitive. The ``.tmp`` file lives for milliseconds and never carries user data
    — the standard atomic-write pattern. Lint-whitelisted within safe_delete.py.

    When ``mode`` is given, ``chmod`` is applied to the ``.tmp`` BEFORE
    the atomic rename, so the final path appears with the requested
    permission bits in a single fs operation — no window where the file
    exists with default umask perms (HATS-467).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(content)
        if mode is not None:
            tmp.chmod(mode)
        tmp.replace(path)
    except OSError as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        if e.errno == errno.ENOSPC:
            raise TrashFullError(
                f"Cannot write {path}: no space left."
            ) from e
        raise


# ---------------------- Manifest ----------------------


def _record(
    session: _Session,
    op: str,
    reason: str,
    orig: Path,
    trash: Path | None,
) -> None:
    """Append entry to in-memory list AND to on-disk MANIFEST.md."""
    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    target = f"{orig} -> {trash}" if trash else f"{orig} (hard delete)"
    session.entries.append(_Entry(ts=ts, op=op, reason=reason, target=target))
    if session.hard_delete:
        return  # no on-disk manifest in hard-delete mode
    manifest = session.root / MANIFEST_NAME
    line = f"{ts} | {op:13s} | {reason or '-':<24s} | {target}\n"
    if not manifest.exists():
        header = (
            f"# ai-hats trash session — {ts}\n"
            f"# pid={os.getpid()}\n"
            f"# Recover: cp -r <relpath> <project>/<relpath>\n"
            "#\n"
            "# Format: <utc-ts> | <op> | <reason> | <orig> -> <trash>\n"
            "\n"
        )
        manifest.write_text(header + line)
    else:
        with manifest.open("a") as f:
            f.write(line)


# ---------------------- Public API ----------------------


def discard(
    path: Path,
    *,
    reason: str = "",
    project_dir: Path | None = None,
) -> Path | None:
    """Move file / dir / symlink to current trash session.

    Args:
        path: Victim path. May be missing (no-op).
        reason: Free-form tag recorded in MANIFEST.md for forensics.
        project_dir: Project root for relative path preservation. Paths
            outside ``project_dir`` land under ``<session>/_external/``.

    Returns:
        Trash destination, or ``None`` if path didn't exist or was
        hard-deleted (under-tmp shortcut, hard-delete env).

    Behaviour:

    * **Missing path** → ``None`` (idempotent — caller can blindly
      retry).
    * **Symlink** → link unlinked, target preserved; sidecar
      ``<dest>.symlink`` records the target string.
    * **Path under $TMPDIR or under current trash root** → direct hard
      delete (avoids трэш-в-трэш recursion). MANIFEST entry tagged
      ``op=clean-tmp``.
    * **Hard-delete mode** (``AI_HATS_TRASH_DIR=-``) → hard delete + WARN
      on stderr.
    * **ENOSPC** → :class:`TrashFullError`.
    """
    if not path.exists() and not path.is_symlink():
        return None

    session = _ensure_session()

    if session.hard_delete:
        _hard_delete(path)
        _record(session, "hard-rm", reason, path, None)
        print(
            f"safe_delete: hard-deleted {path} "
            f"({ENV_TRASH_DIR}=- set, reason={reason or '-'})",
            file=sys.stderr,
        )
        return None

    if _is_under_tmp(path):
        _hard_delete(path)
        _record(session, "clean-tmp", reason, path, None)
        return None

    dest = _resolve_dest(path, project_dir, session)
    _move_to_trash(path, dest)
    _record(session, "discard", reason, path, dest)
    return dest


def replace(
    path: Path,
    new_content: bytes,
    *,
    reason: str = "",
    project_dir: Path | None = None,
    mode: int | None = None,
) -> bool:
    """Snapshot old content to trash, then atomically write ``new_content``.

    Args:
        path: Target file.
        new_content: New bytes to write. (Encode strings explicitly at
            call site — keeps the API honest about what hits disk.)
        reason: Free-form tag recorded in MANIFEST.md.
        project_dir: Project root for relative path preservation.
        mode: Optional octal permission bits applied atomically (e.g.
            ``0o755`` for executables). When ``None`` (default), the
            file inherits the process umask. Applied to the temp file
            BEFORE the atomic rename — no window with default perms
            (HATS-467).

    Returns:
        ``True`` if old content was snapshotted (file existed and bytes
        differ). ``False`` on bytes-identical no-op, fresh file, or
        hard-delete mode.

    Behaviour:

    * **Missing path** → atomic write only (no snapshot). Returns False.
    * **Bytes-identical** → no-op, no session created. Returns False.
      ``mode`` is NOT enforced in this branch — caller must ensure perms
      separately if it matters and the file already exists with the
      right bytes.
    * **Hard-delete mode** → atomic write + WARN, no snapshot. Returns False.
    * **ENOSPC** during snapshot or write → :class:`TrashFullError`.
    """
    if not path.exists():
        _write_atomic(path, new_content, mode=mode)
        return False

    try:
        existing = path.read_bytes()
    except OSError:
        existing = None

    if existing == new_content:
        return False

    session = _ensure_session()

    if session.hard_delete:
        _write_atomic(path, new_content, mode=mode)
        _record(session, "hard-replace", reason, path, None)
        print(
            f"safe_delete: hard-replaced {path} "
            f"({ENV_TRASH_DIR}=- set, reason={reason or '-'})",
            file=sys.stderr,
        )
        return False

    # Snapshot old bytes → trash, then atomic write new in place.
    dest = _resolve_dest(path, project_dir, session)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        if existing is not None:
            dest.write_bytes(existing)
        else:
            # Unreadable but exists — best-effort sentinel.
            dest.write_text("<unreadable on snapshot>\n")
    except OSError as e:
        if e.errno == errno.ENOSPC:
            raise TrashFullError(
                f"Cannot snapshot {path} to trash: no space left."
            ) from e
        raise

    _write_atomic(path, new_content, mode=mode)
    _record(session, "replace", reason, path, dest)
    return True


def hard_delete_mode() -> bool:
    """True when ``AI_HATS_TRASH_DIR=-`` disables the trash (HATS-907: heals
    that promise recoverability must check this BEFORE discarding)."""
    return _resolve_base()[1]


def session_root() -> Path | None:
    """Current trash session root, or ``None`` if no session created yet.

    Returns ``None`` in hard-delete mode too — callers should treat
    "no recoverable artefacts" identically to "no session".
    """
    if _current_session is None:
        return None
    if _current_session.hard_delete:
        return None
    return _current_session.root


def session_summary() -> str | None:
    """One-liner for end-of-operation banner. ``None`` if nothing recorded.

    Surface at the end of bump / init / publish to point users at the
    trash location.
    """
    if _current_session is None or not _current_session.entries:
        return None
    n = len(_current_session.entries)
    if _current_session.hard_delete:
        return (
            f"safe_delete: {n} hard-delete op(s) this run "
            f"({ENV_TRASH_DIR}=- set — not recoverable)."
        )
    return (
        f"safe_delete: {n} op(s) recoverable from {_current_session.root}"
    )


def reset_session() -> None:
    """Clear module state. **Test seam** — production code never calls this."""
    global _current_session
    with _session_lock:
        _current_session = None
