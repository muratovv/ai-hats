"""Consumer add-ons for the rack kernel — the seam HATS-1141 fills.

This module hosted ``HookRunnerExtension``, the executor for the
``lifecycle_hooks`` channel retired in HATS-1147 (ADR-0019 D8).

The pack is now EMPTY BY DESIGN, not deleted. ``rack_cli_provider`` already
passes it to every kernel it builds, so HATS-1141 fills one function body
instead of also having to remember the subscription — and an unsubscribed
check runner is a gate that never fires, the exact hole this retirement closes.
"""  # comment-length: allow — the emptiness is load-bearing; deleting it is the failure mode

from __future__ import annotations

from pathlib import Path

from ai_hats_rack.fsm import Topology


def consumer_subscribers(
    project_dir: Path,
    *,
    tasks_dir: Path | None = None,
    topology: Topology | None = None,
) -> list:
    """The consumer add-on pack for ``build_rack_kernel(extra_subscribers=…)``.

    Empty until HATS-1141 populates it with the check runner. The parameters are
    that runner's inputs, kept so the call site needs no edit when it arrives.
    """
    return []


__all__ = ["consumer_subscribers"]
