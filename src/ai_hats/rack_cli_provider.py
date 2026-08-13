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
from ai_hats_rack.workspace import backlog_selectors_in_root

from .rack_consumers import check_port_factory, consumer_subscribers
from .rack_wiring import build_rack_kernel
from .tracker_wiring import tracker_paths


#: The conventional backlog tail under ``<ai_hats_dir>`` (see ``paths.tasks_dir``).
_TASKS_TAIL = ("tracker", "backlog", "tasks")


def _state_md_for(root: Any) -> Path:
    """This backlog's STATE.md — the OWNER's tracker, never the caller's checkout.

    An anchorless backlog indexes itself: at ``<ai_hats_dir>/STATE.md`` when it
    carries the conventional tail, beside the cards otherwise. Both stay inside
    the backlog the operator named, which is the whole point (HATS-1573).
    """
    if root.backlog_owner is not None:
        return tracker_paths(root.backlog_owner).state_md_path
    if root.tasks_dir.parts[-3:] == _TASKS_TAIL:
        return root.tasks_dir.parents[2] / "STATE.md"
    return root.tasks_dir.parent / "STATE.md"


class CliKernelProvider:
    """The wired-kernel provider handed to ``rack``'s CLI via discovery."""

    def build_kernel(self, root: Any, caller_cwd: Path):
        """The full integrator assembly (mirror of the K6 driver) — kernel +
        every stock extension + the consumer add-on pack (the ``checks:``
        runner, subscribed to THIS definition's topology)."""
        defn = resolve_definition(
            root.tasks_dir,
            prefix_alias=root.prefix,
            project_dir=root.backlog_owner or root.project_dir,
        )
        return build_rack_kernel(
            root.project_dir,
            backlog_owner=root.backlog_owner,
            tasks_dir=root.tasks_dir,
            # The SAME file `after_create` writes: a transition indexes the
            # backlog it moved a card in, never the checkout the operator
            # happens to stand in, whose index it would replace wholesale.
            state_md_path=_state_md_for(root),
            prefix=root.prefix,
            journal_sink=JsonlJournalSink(root.tasks_dir),
            extra_subscribers=consumer_subscribers(
                root.backlog_owner,
                definition=defn,
                catalog=root.tasks_dir,
                known_backlogs=backlog_selectors_in_root(root),
            ),
        )

    def check_port(self, root: Any, catalog: Path):
        """The check executor for ONE mounted catalog of ``root``.

        The half the rack cannot supply: reading the composed rows and spawning
        the script (``subprocess`` is forbidden in that package by an import
        pin). Everything else about the channel — topology, selectors, which
        instances get a subscriber — the rack decides from its own definitions.
        """
        return check_port_factory(root.backlog_owner)(catalog)

    def after_create(self, root: Any, result: Any) -> None:
        """Refresh STATE.md after a create (fork K3 #7): create takes no FSM
        edge, so ``DerivedViewsExtension`` never ran — index the new card once,
        writing the SAME STATE.md the wired kernel's subscriber would."""
        DerivedViewsExtension(
            root.tasks_dir,
            _state_md_for(root),
            topology=resolve_definition(
                root.tasks_dir, prefix_alias=root.prefix, project_dir=root.backlog_owner
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


def _main_repo_cd() -> list[str]:
    """The recipe's ``cd`` line — empty when no project root resolves.

    Merge-time recipes run git in the MAIN repo, while the transition that
    raised is typically invoked from inside the task worktree; the resolver
    hops linked-worktree → main checkout."""
    from ai_hats_rack.resolver import find_project_root

    root = find_project_root(Path.cwd())
    return [f"  cd {root}"] if root is not None else []


def _wt_error_shape(exc: Exception, task_id: str) -> tuple[str, str, list[str]]:
    """(error-code, headline, lines printed under it) per wt exception kind.
    Recipes point at rack verbs (the post-cutover surface); ``wt merge`` stays
    on the ``ai-hats`` binary (the wt engine is not part of the rack)."""
    from ai_hats_wt import (
        WorktreeBaseBranchMismatchError,
        WorktreeDriftError,
        WorktreeMergeAborted,
        WorktreeMergeConsentError,
        WorktreeStateLostError,
    )

    tid = task_id or getattr(exc, "task_id", "") or "<id>"
    branch = getattr(exc, "branch_name", "") or f"task/{tid.lower()}"
    if isinstance(exc, WorktreeMergeAborted):
        # HATS-1540: name the subsystem that refused. HATS-1538 cost a session
        # to a symptom that pointed at plan-gate, so `checks` says so here and
        # the check's own words carry the recipe.
        return ("checks_refused", f"Refused (checks) — cannot merge for {tid}.", [str(exc)])
    if isinstance(exc, WorktreeMergeConsentError):
        return (
            "worktree_merge_consent",
            f"Refused (review consent required) — cannot merge for {tid}. {exc}",
            [
                "The task is ready for review — STOP and hand it off to the supervisor.",
                "Consent is the supervisor's to give, from the environment that launched",
                "this session — an inline prefix on the agent's own command is refused as",
                "a self-grant (HATS-1639). Once review passes (diff seen, notes resolved,",
                "explicit go):",
                "  export AI_HATS_MERGE_ACK=1",
                f"  ai-hats wt merge {branch}",
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
    if isinstance(exc, WorktreeDriftError):
        base = getattr(exc, "base_branch", None) or "<base>"
        wt_path = getattr(exc, "worktree_path", None)
        return (
            "worktree_drift",
            f"Worktree drifted vs original branch — cannot merge for {tid}.",
            [
                str(exc),
                "",
                "Take the new base into the branch, re-verify, then retry:",
                f"  cd {wt_path}" if wt_path else "  cd <worktree>  # ai-hats wt status",
                f"  git rebase {base}",
                *_main_repo_cd(),
                f"  rack transition {tid} --state done",
                "",
                "The rebase clears the guard — no flag needed. To merge the stale "
                "baseline on purpose instead, run "
                f"`ai-hats wt merge --accept-drift {branch}` from the main repo; "
                "--accept-drift belongs to `wt merge`, not `rack transition`.",
            ],
        )
    if isinstance(exc, WorktreeBaseBranchMismatchError):
        return (
            "worktree_base_branch_mismatch",
            f"Refused (base branch mismatch) — cannot merge for {tid}.",
            [
                str(exc),
                "",
                "Switch the main repo to the merge target, then retry:",
                *_main_repo_cd(),
                f"  git checkout {exc.expected}",
                f"  rack transition {tid} --state done",
            ],
        )
    # Any other wt-engine refusal (base-branch, mid-merge, incomplete): typed and
    # loud, but no bespoke recipe — the message carries facts (HATS-1263 Q2).
    return ("worktree_error", f"Refused (worktree) for {tid}: {exc}", [])


__all__ = ["CliKernelProvider", "cli_factory"]
