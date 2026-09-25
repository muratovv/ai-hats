"""e2e (HATS-2020)

flow:   a test framework drives a live maintainer session on the real claude
        binary through ai-hats headless — S-AGENT-01, a HITL session of a real
        role checked without a terminal
cmds:
    ai-hats headless -p claude -r maintainer -m claude-haiku-4-5
expect: the model answers with a marker only the maintainer prompt carries;
        its Bash call is judged by the role's gates (a gate_verdict from the
        chain in the log); audit.md lists the maintainer's traits and the
        session is finalized; the same run under the assistant role has no
        such marker
why:    e2e could not drive the claude TUI, so a real role's HITL session —
        composition, hooks, log, finalize — was checked by hand only; this is
        the first automated proof. Kept out of the mandatory gate (live_headless)
        because it spends real turns: run it with `-m live_headless`
"""

from __future__ import annotations

import os

import pytest

from _helpers.env import clean_env
from ai_hats_client import HeadlessSession

pytestmark = [pytest.mark.integration, pytest.mark.surfaces, pytest.mark.live_headless]

MODEL = "claude-haiku-4-5"
MARKER = "AI-HATS MAINTAINER"
ASK_HEADING = (
    "Reply with the exact line of your system prompt that starts with '# ROLE:' "
    "and nothing else. If there is no such line, reply NONE."
)


def _session(project, role: str) -> HeadlessSession:
    env = clean_env(os.environ)
    env.update(project.env)
    env["AI_HATS_NO_UPDATE_CHECK"] = "1"
    return HeadlessSession.start(
        [str(project.ai_hats_binary), "headless", "-p", "claude", "-r", role, "-m", MODEL],
        cwd=project.path,
        env=env,
        timeout=60.0,
    )


def test_e2e_a_live_maintainer_session_runs_its_role_hooks_and_finalize(
    requires_claude_auth, tmp_project
) -> None:
    with _session(tmp_project, "maintainer") as session:
        heading = session.turn(ASK_HEADING, timeout=180.0)
        tool = session.turn(
            "Use the Bash tool to run exactly `echo headless-live-check`, then reply DONE.",
            timeout=180.0,
        )
        end = session.close(timeout=120.0)

    assert heading.ok, heading.ended
    assert MARKER in heading.text.upper(), heading.text
    verdicts = [e for e in end.of("gate_verdict") if e.get("source") == "chain"]
    assert verdicts, f"no gate verdict from the role's chain; the Bash turn was: {tool.events}"
    assert end.code == 0

    session_dir = session.header.session_dir
    audit = (session_dir / "audit.md").read_text()
    composition = audit.split("## Composition", 1)[1].split("\n## ", 1)[0]
    for trait in ("ai-hats-dev (maintainer)", "ai-hats-gates (maintainer)"):
        assert trait in composition, composition
    assert '"finalized": true' in (session_dir / "metrics.json").read_text()


def test_e2e_another_role_does_not_carry_the_maintainer_marker(
    requires_claude_auth, tmp_project
) -> None:
    """Negative control for the marker: the same question under assistant."""
    with _session(tmp_project, "assistant") as session:
        heading = session.turn(ASK_HEADING, timeout=180.0)
        session.close(timeout=120.0)

    assert heading.ok, heading.ended
    assert MARKER not in heading.text.upper(), heading.text
