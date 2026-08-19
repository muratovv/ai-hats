"""e2e (HATS-1743)

flow:   a supervisor exports a kill switch, then delegates to a sub-agent
cmds:
    AI_HATS_SHARED_STATE_ACK=1 AI_HATS_YOLO=1 ai-hats agent <role> -p holdfast --task hold
expect: the surface child sees both flags BLANK, while the session identity arrives intact
why:    an approval is scoped to the session it was given in — `rule_pause_before_shared_state_write`
        calls the export "pre-approving the whole session" and ADR-0023 "the shell that launched
        the session". A sub-agent is a different session: own id, own dir, own composition.
        Only a real launch can answer this: on the SDK road the child's environment is the
        transport's to build, and no in-process assertion on the returned dict observes it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_hats.constants import BYPASS_FLAGS_NOT_INHERITED

from _helpers.fake_surface import install

pytestmark = pytest.mark.integration

#: Two of the fifteen, picked because the journal shows them most: SHARED_STATE_ACK
#: leads every count and YOLO switches off the guard wholesale.
EXPORTED = {"AI_HATS_SHARED_STATE_ACK": "1", "AI_HATS_YOLO": "1"}


@pytest.fixture
def child_env(tmp_project, tmp_path: Path, repo_root: Path) -> dict:
    """The environment the sub-agent's surface process actually received."""
    surface = install(tmp_project, tmp_path, repo_root)
    dump = tmp_path / "child-env.json"
    surface.run_agent_once(
        role="assistant", extra_env={**EXPORTED, "FAKE_SURFACE_ENV_DUMP": str(dump)}
    )
    assert dump.is_file(), "the surface child never ran, so nothing was measured"
    return json.loads(dump.read_text())


def test_a_sub_agent_does_not_inherit_the_supervisors_exported_approvals(child_env) -> None:
    """Every withheld flag arrives blank — not "1", and not the supervisor's value."""
    carried = {
        flag: child_env[flag]
        for flag in BYPASS_FLAGS_NOT_INHERITED
        if child_env.get(flag) not in (None, "")
    }
    assert not carried, (
        f"the child inherited approvals the supervisor gave THEIR session: {carried}. "
        "The launch must blank them — see assemble_launch_env (HATS-1743)."
    )


def test_the_withholding_does_not_strip_the_session_the_child_needs(child_env) -> None:
    """The counterweight: blanking is aimed at approvals, not at the environment.

    Without this a green sibling would be satisfied by a child that inherited
    nothing at all — which is not the contract and would break every gate that
    resolves a session.
    """
    assert child_env.get("AI_HATS_SESSION_ID"), "the child lost its session identity"
    assert child_env.get("AI_HATS_SESSION_IDENTITY"), "the child lost the identity envelope"
