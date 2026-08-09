"""No second live CLI surface reaches the bound points (HATS-1540 R7).

HATS-1159 retired ``ai-hats task``, the legacy mirror of the tracker CLI. That
matters to the ``checks:`` channel specifically: a second surface driving the
same FSM would take transitions the ``rack`` kernel never sees, so every gate
bound to an edge would be armed on one road and absent on the other — the exact
asymmetry ``wt:pre-merge`` exists to close on the worktree side.

It was verified by hand on 2026-08-09 and that is why it is a test now: an
observation rots in silence, and this one rots the moment someone re-registers a
convenience alias.
"""

from __future__ import annotations

import pytest

#: Retired tracker surfaces. ``rack`` is the one CLI that drives the FSM, and it
#: is a separate binary — not a subcommand of this one.
RETIRED = ["task", "tasks", "backlog", "tracker"]


@pytest.mark.parametrize("name", RETIRED)
def test_the_ai_hats_binary_registers_no_tracker_command(name: str):
    from ai_hats.cli import main

    assert name not in main.commands, (
        f"`ai-hats {name}` is registered again — a second surface onto the FSM "
        f"takes transitions the rack kernel never sees, so every `edge:` binding "
        f"is armed on one road and silently absent on the other (HATS-1159)"
    )


def test_the_worktree_group_is_the_only_merge_surface():
    """``wt merge`` is where ``wt:pre-merge`` fires, so no sibling may merge.

    A second command that reaches ``WorktreeManager.merge`` would still fire the
    point — the core fires it, not the caller — but a command that merged some
    other way would not, and that is what this pins.
    """
    from ai_hats.cli import main

    assert "wt" in main.commands
    assert "merge" not in main.commands, "a top-level `merge` would be a second road"
