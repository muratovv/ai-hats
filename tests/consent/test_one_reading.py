"""HATS-1816 — the session wrapper and the PreToolUse gate read a verb ONCE.

Two earlier fixes in this class shared data and left the second reading standing
(HATS-1754, HATS-1781). The differential below is what makes a third divergence
impossible to write: it asks both readers the same question on every corpus row.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HOOKS = (
    Path(__file__).resolve().parents[2]
    / "packages/ai-hats-library/src/ai_hats_library/core/skills/safety-guard/hooks"
)
sys.path.insert(0, str(HOOKS))

from ai_hats.consent_wrapper import match_operation  # noqa: E402
from ai_hats_library.hooks.consent_gate import operations  # noqa: E402
from safety_gate import merge_branch, transition_target  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

from corpus import CORPUS  # noqa: E402

POLICY = {"rack.transition": ("plan->execute", "->done"), "wt.merge": ("pre-merge",)}
GUARDED_TARGETS = {"execute", "done"}


def _wrapper(row):
    matched = match_operation(row.surface, list(row.argv), POLICY, source_state=row.source)
    return None if matched is None else (matched.operation.type, matched.operation.subject)


def _gate(row):
    if row.surface == "rack":
        task_id, target = transition_target([row.surface, *row.argv])
        return ("rack.transition", task_id) if target in GUARDED_TARGETS else None
    branch = merge_branch([row.surface, *row.argv])
    return ("wt.merge", branch) if branch else None


@pytest.mark.parametrize("row", CORPUS, ids=lambda r: " ".join((r.surface, *r.argv)))
def test_both_readers_answer_the_same(row):
    """The wrapper and the gate agree on WHAT this call is — verb and subject."""
    assert _wrapper(row) == _gate(row)


@pytest.mark.parametrize(
    ("argv", "source", "expected"),
    [
        # a flag before the positional id used to hide the move from the wrapper
        (("transition", "--tasks-dir", "/t", "HATS-1", "execute"), "plan", "HATS-1"),
        # ... and used to bind the ticket to the FLAG NAME instead of the task
        (("transition", "--state", "execute", "HATS-1"), "plan", "HATS-1"),
    ],
)
def test_a_flag_before_the_id_does_not_hide_the_move(argv, source, expected):
    matched = match_operation("rack", list(argv), POLICY, source_state=source)
    assert matched is not None
    assert matched.operation.subject == expected


def test_a_flag_before_the_subcommand_does_not_hide_a_merge():
    matched = match_operation("ai-hats", ["--verbose", "wt", "merge", "task/x"], POLICY)
    assert matched is not None
    assert matched.operation.subject == "task/x"


def test_a_note_about_a_move_is_not_the_move():
    """`--log execute` is text ABOUT the transition, never the transition."""
    assert (
        match_operation(
            "rack", ["transition", "X", "--log", "execute"], POLICY, source_state="plan"
        )
        is None
    )
    assert transition_target(["rack", "transition", "X", "--log", "execute"]) == ("X", "")


def test_source_state_still_narrows_the_arrow():
    """HATS-1813 must survive the move to the registry."""
    argv = ["transition", "HATS-1", "execute"]
    assert match_operation("rack", argv, POLICY, source_state="plan") is not None
    assert match_operation("rack", argv, POLICY, source_state="review") is None


def test_an_operation_with_no_surface_must_say_why():
    """Hook-only is a stated property, never a null left to interpretation."""
    with pytest.raises(ValueError, match="must say why"):
        operations.OperationSpec(
            type="probe",
            surface=None,
            read=lambda _a: None,
            selector_reason=lambda _s: None,
            admits=lambda *_a: False,
            legacy_flags=lambda _r: (),
        )


def test_hook_only_operations_materialise_no_shim():
    assert operations.wrapped_surfaces(["rack.transition", "wt.merge"]) == ["ai-hats", "rack"]
    assert operations.wrapped_surfaces(["nothing.declared"]) == []
