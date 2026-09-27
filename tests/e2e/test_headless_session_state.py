"""e2e (HATS-2036)

flow:   a chat drives a headless session and keeps a status line: the model,
        the mode and how full the context is
cmds:
    ai-hats headless -p claude -r assistant
expect: the first turn says the session's configuration once, before its
        response; every turn the model answered says how full the context is
        right before its turn_ended; a turn with the same configuration says
        none, a slash command claude runs itself says no fill, and the turn
        after /model says the new model
why:    the configuration rides claude's system/init and the fill its result,
        both of which the holder dropped before; a chat waking on turn_ended
        must already hold the fill of that turn
"""

from __future__ import annotations

import pytest

from ai_hats_client import HeadlessSession
from ai_hats_client.testing import install
from ai_hats_client.testing.stub_claude import CONTEXT_WINDOW

from _helpers.headless import session_env

pytestmark = [pytest.mark.integration, pytest.mark.surfaces]


@pytest.fixture
def session_on_stub(tmp_project, tmp_path):
    stub = install(tmp_path)

    def start() -> HeadlessSession:
        return HeadlessSession.start(
            [str(tmp_project.ai_hats_binary), "headless", "-p", "claude", "-r", "assistant"],
            cwd=tmp_project.path,
            env=session_env(stub, tmp_project),
        )

    return start


def _kinds(turn) -> list[str]:
    return [e["event"] for e in turn.events if e["event"] not in ("signal", "run_started")]


def test_e2e_a_status_line_reads_the_configuration_and_the_fill_off_the_log(
    session_on_stub,
) -> None:
    with session_on_stub() as session:
        first = session.turn("hello")
        second = session.turn("again")
        switch = session.turn("/model stub-model-2")
        after = session.turn("and now")
        session.close()

    kinds = _kinds(first)
    assert kinds.index("prompt_received") < kinds.index("session_configured")
    assert kinds.index("session_configured") < kinds.index("response_started")
    (configured,) = first.of("session_configured")
    (started,) = first.of("response_started")
    assert configured["model"] == started["model"]
    assert (configured["planning"], configured["permission_mode"]) == (False, "default")
    assert "model" in configured["commands"]

    for turn in (first, second, after):
        assert _kinds(turn)[-2:] == ["context_usage", "turn_ended"]
        (filled,) = turn.of("context_usage")
        assert filled["used_tokens"] > 0 and filled["max_tokens"] == CONTEXT_WINDOW

    assert not second.of("session_configured"), "the same init twice is one configuration"
    assert not switch.of("context_usage"), "no model read anything for /model"
    (switched,) = after.of("session_configured")
    assert switched["model"] == "stub-model-2"
