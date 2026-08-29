"""What claude is, as data — see :class:`SurfaceProfile`."""

from __future__ import annotations

from ..hook_channel import BINDABLE_EVENTS, Dialect, SurfaceProfile

PROFILE = SurfaceProfile(
    label="claude",
    # Empty, and it is the one row that says something BY being empty: the
    # matcher vocabulary is claude's own vocabulary, so there is nothing to
    # translate. `matcher_names` then answers `(native,)` and `spoken_name`
    # answers the native name — right by the fallback branch rather than by a
    # table, which is why a test drives both directly instead of iterating a
    # row that has nothing in it.
    tool_names={},
    arg_names={},
    native_events=BINDABLE_EVENTS,
    manifest_subpath=(),
    #: The session plugin's skills dir — `claude_plugin_skills_dir` of the
    #: cache's `plugin`, which is where the mirror the entries point into sits.
    skills_subpath=("plugin", "skills"),
    # Every one measured on 2.1.247, not assumed (poc-hook-delivery.md M6):
    # `ask` blocks and asks, `updatedInput` is applied, `deny` blocks with the
    # reason quoted, `additionalContext` reaches the model. The ticket pair is
    # what `safety_gate.py` has been emitting on this surface all along.
    speaks=Dialect(
        can_ask=True, can_ask_with_ticket=True, can_deny_after=True, can_carry_nudges=True
    ),
)

__all__ = ["PROFILE"]
