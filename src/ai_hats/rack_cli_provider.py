"""Integrator wiring for the ``rack`` CLI, discovered via the
``ai_hats_rack.kernel_factory`` entry point (HATS-1038 C1).

The rack NEVER imports the integrator (import-hygiene pin); it loads THIS
factory by metadata and calls back through the duck-typed provider, so the
bare ``rack`` binary becomes the fully-wired production entry when the
integrator is installed and degrades to the bare kernel standalone. Supplies
the wired kernel, a post-create ``views.refresh()`` (create takes no FSM
edge), and typed wt-exception rendering (no raw teardown traceback).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from ai_hats_rack.cli_common import emit_json
from ai_hats_rack.definition import resolve_definition
from ai_hats_rack.extensions import DerivedViewsExtension
from ai_hats_rack.journal import JsonlJournalSink

from .rack_consumers import consumer_subscribers
from .rack_wiring import build_rack_kernel
from .tracker_wiring import tracker_paths


class CliKernelProvider:
    """The wired-kernel provider handed to ``rack``'s CLI via discovery."""

    def build_kernel(self, root: Any, caller_cwd: Path):
        """The full integrator assembly (mirror of the K6 driver) — kernel +
        every stock extension + the consumer hook-runner."""
        defn = resolve_definition(
            root.tasks_dir, prefix_alias=root.prefix, project_dir=root.project_dir
        )
        return build_rack_kernel(
            root.project_dir,
            tasks_dir=root.tasks_dir,
            prefix=root.prefix,
            journal_sink=JsonlJournalSink(root.tasks_dir),
            extra_subscribers=consumer_subscribers(
                root.project_dir, tasks_dir=root.tasks_dir, topology=defn.topology
            ),
        )

    def after_create(self, root: Any, result: Any) -> None:
        """Refresh STATE.md after a create (fork K3 #7): create takes no FSM
        edge, so ``DerivedViewsExtension`` never ran — index the new card once,
        writing the SAME STATE.md the wired kernel's subscriber would."""
        DerivedViewsExtension(
            root.tasks_dir,
            tracker_paths(root.project_dir).state_md_path,
            topology=resolve_definition(
                root.tasks_dir, prefix_alias=root.prefix, project_dir=root.project_dir
            ).topology,
        ).refresh()

    def handle_error(self, exc: Exception, as_json: bool, task_id: str = "") -> bool:
        """Typed rendering of the wt-engine exception family. Returns True iff
        this provider owned ``exc`` (rendered it) — the CLI then exits 1; False
        lets the rack's own typed handler take it."""
        return _render_wt_error(exc, as_json, task_id)


def cli_factory() -> CliKernelProvider:
    """Entry-point target (``ai_hats_rack.kernel_factory``)."""
    return CliKernelProvider()


def _render_wt_error(exc: Exception, as_json: bool, task_id: str) -> bool:
    """Render an ``ai_hats_wt`` exception as a typed refusal (never a raw
    traceback). Any non-wt exception returns False untouched."""
    if not type(exc).__module__.startswith("ai_hats_wt"):
        return False
    code, headline, recipe = _wt_error_shape(exc, task_id)
    if as_json:
        emit_json({"error": {"code": code, "message": str(exc)}})
    else:
        click.echo(f"error: {headline}", err=True)
        for line in recipe:
            click.echo(line, err=True)
    return True


def _wt_error_shape(exc: Exception, task_id: str) -> tuple[str, str, list[str]]:
    """(error-code, headline, copy-paste recipe) per wt exception kind. Recipes
    point at rack verbs (the post-cutover surface); ``wt merge`` stays on the
    ``ai-hats`` binary (the wt engine is not part of the rack)."""
    from ai_hats_wt import WorktreeMergeConsentError, WorktreeStateLostError

    tid = task_id or getattr(exc, "task_id", "") or "<id>"
    branch = getattr(exc, "branch_name", "") or f"task/{tid.lower()}"
    if isinstance(exc, WorktreeMergeConsentError):
        return (
            "worktree_merge_consent",
            f"Refused (review consent required) — cannot merge for {tid}. {exc}",
            [
                "The task is ready for review — STOP and hand it off to the supervisor.",
                "Once review passes (diff seen, notes resolved, explicit go), merge and retry:",
                f"  AI_HATS_MERGE_ACK=1 ai-hats wt merge {branch}",
                f"  rack transition {tid} --state done",
            ],
        )
    if isinstance(exc, WorktreeStateLostError):
        return (
            "worktree_state_lost",
            f"Refused (worktree state lost) — task {tid} cannot be silently marked DONE. {exc}",
            [
                f"Branch '{branch}' has commits that are NOT in the base branch "
                "(an already-merged branch finalizes on its own — HATS-697).",
                "Apply the un-merged work, then finalize:",
                f"  git merge --no-ff {branch}",
                f"  rack transition {tid} --state done",
            ],
        )
    # Any other wt-engine refusal (base-branch, mismatch, mid-merge, incomplete,
    # drift): typed and loud, but no bespoke recipe — the message carries facts.
    return ("worktree_error", f"Refused (worktree) for {tid}: {exc}", [])


__all__ = ["CliKernelProvider", "cli_factory"]
