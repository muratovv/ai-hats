"""Guard: rack tests run with no ambient ai-hats session (HATS-1049).

Without conftest's ``_isolate_session_env`` autouse fixture, running the suite
inside a live ai-hats session leaks ``AI_HATS_SESSION_ID`` into the HATS-955
single-slot ownership check, so cross-task transition tests
(``test_transition_ops::test_cli_attach_before_state_vs_reverse``) fail — absent
in CI, present under worktree-isolated maintainer runs. This pins the invariant.
"""

from __future__ import annotations

import os


def test_session_env_is_isolated():
    assert os.environ.get("AI_HATS_SESSION_ID") is None
    assert os.environ.get("AI_HATS_ROOT_PID") is None
