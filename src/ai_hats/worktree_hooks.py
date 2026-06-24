"""Worktree lifecycle hook execution (HATS-823, ADR-0012 D7).

Runs a single component-declared ``wt_in`` / ``wt_out`` script under a bounded,
fail-safe execution contract. This module owns the *mechanism*; the worktree
manager owns the *policy* (``wt_out`` fail-closed vs ``wt_in`` warn-continue).

Contract (D7):

- **Bounded timeout.** Default kept *below* the lifecycle-lock budget so a hung
  hook times out and releases the lock before a peer ``wt`` op on the same branch
  hits ``WorktreeLockError`` and mis-blames a concurrent op (HATS-711 class).
  Overridable via ``AI_HATS_WT_HOOK_TIMEOUT_S``.
- **stdin closed** (``DEVNULL``): an interactive ``read`` fails fast, never hangs.
- **cwd = project_dir**: scripts use the ``AI_HATS_*`` env paths, not ambient cwd.
- **Memory-safe output**: child stdout/stderr stream straight to a managed log
  file (never buffered into the parent's memory); runtime is bounded by the
  timeout.
- **Missing / non-executable / non-zero / timeout** all yield a failed
  :class:`HookOutcome` — the caller decides fail-closed (``wt_out``) vs
  warn-continue (``wt_in``).
- **SIGINT is not swallowed**: ``KeyboardInterrupt`` propagates so the operator
  can abort a teardown (worktree preserved upstream).
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .worktree_locks import LIFECYCLE_LOCK_TIMEOUT

logger = logging.getLogger(__name__)

# Default per-hook wall-clock budget. Strictly below LIFECYCLE_LOCK_TIMEOUT so
# the timeout — not the lock — is what bounds a hung hook (see module docstring).
WT_HOOK_TIMEOUT_S: float = 45.0
_TIMEOUT_ENV = "AI_HATS_WT_HOOK_TIMEOUT_S"

# Load-bearing D7 invariant (explicit raise, not assert — survives ``python -O``):
# the default hook budget MUST stay under the lifecycle-lock timeout so a hung
# hook times out before a peer waiting on the lock mis-blames a concurrent op.
if WT_HOOK_TIMEOUT_S >= LIFECYCLE_LOCK_TIMEOUT:  # pragma: no cover - config guard
    raise RuntimeError(
        f"WT_HOOK_TIMEOUT_S ({WT_HOOK_TIMEOUT_S}) must be < "
        f"LIFECYCLE_LOCK_TIMEOUT ({LIFECYCLE_LOCK_TIMEOUT})"
    )


def resolve_hook_timeout() -> float:
    """The effective per-hook timeout: ``AI_HATS_WT_HOOK_TIMEOUT_S`` or default.

    A missing / non-numeric / non-positive override falls back to the default
    (fail-safe — a typo must not disable the bound).
    """
    raw = os.environ.get(_TIMEOUT_ENV)
    if not raw:
        return WT_HOOK_TIMEOUT_S
    try:
        val = float(raw)
    except ValueError:
        return WT_HOOK_TIMEOUT_S
    return val if val > 0 else WT_HOOK_TIMEOUT_S


def collect_carry_for_role(
    project_dir: Path, role: str = ""
) -> dict[str, list[dict[str, object]]]:
    """Compose the effective role and return its serialized worktree carry.

    The threading entry point (HATS-823 D3): the create-time caller
    (``state._setup_worktree`` / ``wt create`` CLI) calls this to collect the
    worktree hooks the worktree's role declares, to pass into
    ``WorktreeManager.create(wt_hooks=...)``. ``role`` falls back to the
    project's ``active_role`` / ``default_role`` (the canonical effective-role
    resolution). Compose failures degrade to an empty carry with a WARN —
    collection trouble must not block worktree creation; a genuinely
    declared-but-missing script still fail-closes at teardown (D7).
    """
    from .assembler import Assembler
    from .composer import collect_worktree_hooks
    from .materialize import compose_for_role

    try:
        assembler = Assembler(project_dir=project_dir)
        cfg = assembler.project_config
        effective = role or cfg.active_role or cfg.default_role
        if not effective:
            return {}
        result = compose_for_role(assembler, effective)
        return serialize_collected_hooks(collect_worktree_hooks(result))
    except Exception as exc:  # noqa: BLE001 — never block create on carry collection
        logger.warning(
            "worktree hooks: could not compose role %r for carry collection: %s",
            role,
            exc,
        )
        return {}


def serialize_collected_hooks(
    collected: dict[str, list[tuple[str, object]]],
) -> dict[str, list[dict[str, object]]]:
    """Flatten ``collect_worktree_hooks`` output into a JSON-safe carry record.

    ``{kind: [(skill, WorktreeHook)]}`` → ``{kind: [{skill, script, on?}]}`` —
    the shape persisted in worktree state and consumed by the manager's run
    methods at create / teardown (HATS-823). ``on`` is omitted for ``wt_in``
    (always empty) and for any leaf with an empty ``on``.
    """
    out: dict[str, list[dict[str, object]]] = {}
    for kind, entries in collected.items():
        rows: list[dict[str, object]] = []
        for skill_name, hook in entries:
            row: dict[str, object] = {"skill": skill_name, "script": hook.script}
            if getattr(hook, "on", ()):  # wt_out carries teardown events
                row["on"] = list(hook.on)
            rows.append(row)
        if rows:
            out[kind] = rows
    return out


@dataclass(frozen=True)
class HookOutcome:
    """Result of one hook run. ``ok`` drives the caller's fail-closed decision."""

    ok: bool
    exit_code: int | None
    reason: str


def run_worktree_hook(
    script: Path,
    *,
    event: str,
    worktree_path: Path,
    project_dir: Path,
    branch_name: str,
    timeout: float | None = None,
    log_path: Path | None = None,
) -> HookOutcome:
    """Run one worktree hook ``script`` under the D7 contract.

    Returns a :class:`HookOutcome`; never raises on hook *failure*.
    ``KeyboardInterrupt`` (SIGINT) is intentionally allowed to propagate.
    """
    if timeout is None:
        timeout = resolve_hook_timeout()
    if not script.is_file():
        return HookOutcome(False, None, f"hook script missing: {script}")
    if not os.access(script, os.X_OK):
        return HookOutcome(False, None, f"hook script not executable: {script}")

    env = {
        **os.environ,
        "AI_HATS_WORKTREE_PATH": str(worktree_path),
        "AI_HATS_PROJECT_DIR": str(project_dir),
        "AI_HATS_BRANCH_NAME": branch_name,
        "AI_HATS_EVENT": event,
    }

    log_fh = None
    try:
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_fh = open(log_path, "wb")
            log_fh.write(
                f"# wt-hook event={event} script={script} timeout={timeout}s\n".encode()
            )
            log_fh.flush()
        out_target = log_fh if log_fh is not None else subprocess.DEVNULL
        try:
            proc = subprocess.run(
                [str(script)],
                cwd=str(project_dir),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=out_target,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return HookOutcome(
                False, None, f"hook timed out after {timeout}s: {script}"
            )
        except (FileNotFoundError, OSError) as e:
            return HookOutcome(
                False, None, f"hook could not run ({type(e).__name__}): {e}"
            )
    finally:
        if log_fh is not None:
            log_fh.close()

    if proc.returncode != 0:
        return HookOutcome(
            False, proc.returncode, f"hook exited {proc.returncode}: {script}"
        )
    return HookOutcome(True, 0, "ok")
