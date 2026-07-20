"""Shared CLI kernel-wiring + result rendering for the verb commands (HATS-1036).

Lifted out of ``cli.py`` so the ``verbs/`` package can reach the provider
discovery, wired/bare kernel selection, workspace routing, and the delta/result
renderers WITHOUT importing the command registry (``cli.main``) — cli.py stays
the aggregator and re-exports these under their historical ``cli._*`` names, so
the dotted-path pins (test_cli_wiring / test_error_surface) survive untouched.
"""

from __future__ import annotations

import importlib.metadata
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import click

from .cli_common import resolved_root as _resolved_root
from .composition import build_card_schema, stock_validators
from .definition import resolve_definition
from .dispatch import bind_subscribers, validate_requires_states
from .extensions import standalone_extensions
from .journal import JsonlJournalSink
from .kernel import Kernel, KernelResult
from .resolver import RackRoot
from .workspace import Workspace

#: Entry-point group the integrator advertises its wired-kernel factory under.
#: Discovery (not a static import) keeps the import-hygiene pin intact.
KERNEL_FACTORY_GROUP = "ai_hats_rack.kernel_factory"


@runtime_checkable
class KernelProvider(Protocol):
    """Integrator-contributed wiring, discovered by entry point (HATS-1038 C1)."""

    def build_kernel(self, root: RackRoot, caller_cwd: Path) -> Kernel: ...
    def after_create(self, root: RackRoot, result: KernelResult) -> None: ...
    def handle_error(self, exc: Exception, as_json: bool, task_id: str = ...) -> bool: ...


@lru_cache(maxsize=1)
def _provider() -> KernelProvider | None:
    """First registered wiring factory, or None → bare standalone kernel."""
    for ep in importlib.metadata.entry_points(group=KERNEL_FACTORY_GROUP):
        return ep.load()()
    return None


def _bare_kernel(root: RackRoot) -> Kernel:
    # Standalone mutation surface = kernel + scaffold + plan-gate (epic §2.3):
    # the composite transition still enforces the gate; no ownership/worktree.
    # One backlog definition builds the kernel AND its subscribers (HATS-1042).
    defn = resolve_definition(root.tasks_dir, prefix_alias=root.prefix, project_dir=root.project_dir)
    subscribers = standalone_extensions(root.tasks_dir, definition=defn)
    validate_requires_states(subscribers, defn.topology, source=str(root.tasks_dir))
    kernel = Kernel(
        root.tasks_dir,
        prefix=defn.prefix,
        topology=defn.topology,
        registry=defn.links_registry,
        edge_names=defn.edge_names,
        schema=build_card_schema(defn, stock_validators()),
        subscribers=subscribers,
        journal_sink=JsonlJournalSink(root.tasks_dir),
    )
    bind_subscribers(subscribers, kernel)
    return kernel


def _build_kernel(
    tasks_dir: Path | None, caller_cwd: Path, provider: KernelProvider | None
) -> tuple[Kernel, RackRoot]:
    """Resolve the root, then build the wired kernel (integrator present) or the
    bare standalone kernel. Every CLI-built kernel journals to audit.jsonl (K7)."""
    root = _resolved_root(tasks_dir, caller_cwd)
    kernel = provider.build_kernel(root, caller_cwd) if provider is not None else _bare_kernel(root)
    return kernel, root


def _workspace(
    tasks_dir: Path | None, caller_cwd: Path, provider: KernelProvider | None
) -> tuple[Workspace, RackRoot]:
    """Discover the workspace and wire the tasks-instance builder (HATS-1044): the
    integrator (or bare) kernel is the tasks catalog's code channel; sibling
    backlogs fall through to the portable kit (``None`` return)."""
    root = _resolved_root(tasks_dir, caller_cwd)

    def _builder(instance: Any) -> Kernel | None:
        if not instance.is_tasks:
            return None  # portable default (workspace-injected cross-backlog checker)
        return provider.build_kernel(root, caller_cwd) if provider is not None else _bare_kernel(root)

    return Workspace.discover([root], kernel_builder=_builder), root


def _echo_deltas(result: KernelResult) -> None:
    """Print work_log deltas subscribers produced this op — the worktree path
    (in-lock) and epic auto-transitions (post-lock, journal-only) — inline, not
    only via a follow-up read (HATS-1038 C1)."""
    seen: set[str] = set()
    for record in result.journal:
        for outcome in record.outcomes:
            if outcome.delta is None:
                continue
            for line in outcome.delta.work_log:
                if line not in seen:
                    seen.add(line)
                    click.echo(f"  {line}")


def _result_payload(result: KernelResult) -> dict[str, Any]:
    return {
        "task": result.task.to_dict(),
        "transitions": [t.to_dict() for t in result.transitions],
        "journal": [r.to_dict() for r in result.journal],
        "ops": [dict(op) for op in result.ops],
    }
