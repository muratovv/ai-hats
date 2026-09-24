"""e2e (HATS-2028)

flow:   a program sends prompts with its own ids and finds each turn's end by
        id — while the surface folds a prompt into the running turn, or starts
        a turn of its own
cmds:
    ai-hats headless -p claude -r assistant
expect: a folded prompt ends with the one it joined, in one turn_ended listing
        both ids; a turn the surface began lists none; a repeated id or one
        not in canonical UUID form is refused as command_rejected; the header
        names the log's format and the commands it takes
why:    counting turn_ended lines breaks on a fold and on a turn with no prompt;
        an id cannot, and a repeated one would wait forever on claude
"""

from __future__ import annotations

import pytest

from _helpers.headless_client import HeadlessSession, prompt_command
from _helpers.stub_claude import install

pytestmark = [pytest.mark.integration, pytest.mark.surfaces]


@pytest.fixture
def session_on_stub(tmp_project, tmp_path):
    stub = install(tmp_path)

    def start(*args: str) -> HeadlessSession:
        return HeadlessSession.start(
            [str(tmp_project.ai_hats_binary), "headless", "-p", "claude", "-r", "assistant", *args],
            cwd=tmp_project.path,
            env=stub.session_env(tmp_project),
        )

    return start


def test_e2e_a_folded_prompt_ends_in_the_turn_it_joined(session_on_stub) -> None:
    with session_on_stub() as session:
        first = session.prompt("@fold")
        second = session.prompt("and this too")
        turn = session.turn_for(first)
        joined = session.turn_for(second, timeout=5)
        session.close()

    assert joined is turn, "one turn_ended answers both prompts"
    assert turn.prompt_ids == (first, second)
    assert [e["prompt_id"] for e in turn.of("prompt_received")] == [first, second]


def test_e2e_a_turn_the_surface_began_answers_no_prompt(session_on_stub) -> None:
    with session_on_stub() as session:
        mine = session.turn("@bg")
        own = session.next_turn()
        session.close()

    assert mine.text == "started"
    assert own.prompt_ids == () and own.text == "background done"
    assert own.of("prompt_received") == (), "the wire has no echo for it"


def test_e2e_a_repeated_or_malformed_id_is_refused(session_on_stub) -> None:
    with session_on_stub() as session:
        used = session.prompt("one")
        session.turn_for(used)
        session.prompt("two", id=used)
        session.send_raw(prompt_command("three", "NOT-A-UUID"))
        after = session.turn("four")
        session.close()

    refused = after.signals("command_rejected")
    assert [e["raw_code"] for e in refused] == ["prompt", "prompt"]
    assert "already used" in refused[0]["detail"]
    assert '"id" must be a UUID' in refused[1]["detail"]
    assert [e["text"] for e in after.of("prompt_received")] == ["four"], "neither reached claude"


def test_e2e_the_header_names_the_log_format_and_the_commands(session_on_stub) -> None:
    with session_on_stub("hello") as session:
        turn = session.next_turn()
        session.close()

    assert (session.header.events, session.header.commands) == ("events/v1", ("prompt",))
    (received,) = turn.of("prompt_received")
    assert turn.prompt_ids == (received["prompt_id"],), "the positional prompt has an id too"
