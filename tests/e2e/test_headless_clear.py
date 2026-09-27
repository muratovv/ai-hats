"""e2e (HATS-2032)

flow:   a program sends a headless session a prompt, /clear and another
        prompt; claude goes on after /clear under a new session id and a new
        transcript
cmds:
    ai-hats headless -p claude -r assistant
expect: /clear is a context_cleared signal; metrics.json names both session
        ids, the launch's first; the audit and the usage read both transcripts
why:    with one id the finalize read only what came before /clear
"""

from __future__ import annotations

import json

import pytest

from ai_hats_client import HeadlessSession
from ai_hats_client.testing import install

from _helpers.headless import session_env

pytestmark = [pytest.mark.integration, pytest.mark.surfaces]


def test_e2e_a_session_that_clears_is_recorded_in_both_its_transcripts(
    tmp_project, tmp_path
) -> None:
    stub = install(tmp_path)
    with HeadlessSession.start(
        [str(tmp_project.ai_hats_binary), "headless", "-p", "claude", "-r", "assistant"],
        cwd=tmp_project.path,
        env=session_env(stub, tmp_project),
    ) as session:
        session.turn("BEFORE-CLEAR")
        cleared = session.turn("/clear")
        session.turn("AFTER-CLEAR")
        session.close()

    [signal] = cleared.signals("context_cleared")
    assert signal["raw_code"] == "conversation_reset"

    metrics = json.loads((session.header.session_dir / "metrics.json").read_text())
    launch, moved = metrics["claude_session_ids"]
    assert launch == metrics["claude_session_id"] == session.header.provider_session_id
    assert list(stub.config_dir.glob(f"projects/*/{moved}.jsonl")), "the stub moved as claude does"

    audit = (session.header.session_dir / "audit.md").read_text()
    assert "BEFORE-CLEAR" in audit and "AFTER-CLEAR" in audit, audit
    usage = json.loads((session.header.session_dir / "usage.json").read_text())
    assert usage["api_calls"] == 2, "one model call on each side of /clear"
