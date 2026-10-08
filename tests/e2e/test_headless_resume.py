"""e2e (HATS-2058)

flow:   a chat's holder is gone (a redeploy took it); the next prompt starts a
        new holder on the same conversation, under the same claude session id
cmds:
    ai-hats headless -p claude -r assistant "first turn"
    ai-hats headless -p claude -r assistant --resume <provider_session_id> "next turn"
expect: the binary is put back on that conversation (--resume <id>, no
        --session-id) and answers from it; the header and metrics.json name
        the same id; the new log holds this run's turn alone. A --resume that
        is no UUID, or names no conversation under the cwd, is refused with
        exit 2 before anything starts
why:    the holder refused --resume, and without a launch id a resumed session
        had no event log, so a relay could not go on with a chat it had lost
"""  # comment-length: allow — the e2e catalog header format

from __future__ import annotations

import json

import pytest

from ai_hats_client import HeadlessSession, SessionEnded
from ai_hats_client.testing import install

from _helpers.headless import session_env

pytestmark = [pytest.mark.integration, pytest.mark.surfaces]


def _argv(project, *args: str) -> list[str]:
    return [str(project.ai_hats_binary), "headless", "-p", "claude", "-r", "assistant", *args]


def test_e2e_a_new_holder_goes_on_with_the_conversation_under_its_id(tmp_project, tmp_path) -> None:
    stub = install(tmp_path)
    env = session_env(stub, tmp_project)
    with HeadlessSession.start(
        _argv(tmp_project, "the code word is PINEAPPLE"), cwd=tmp_project.path, env=env
    ) as earlier:
        earlier.next_turn()
        earlier.close()
    sid = earlier.header.provider_session_id

    with HeadlessSession.start(
        _argv(tmp_project, "--resume", sid, "@recall"), cwd=tmp_project.path, env=env
    ) as resumed:
        turn = resumed.next_turn()
        end = resumed.close()

    assert end.code == 0
    assert turn.text == "the code word is PINEAPPLE", "the binary answered from the conversation"
    assert resumed.header.provider_session_id == sid
    argv = stub.argvs()[-1]
    assert argv[argv.index("--resume") + 1] == sid
    assert "--session-id" not in argv, "claude refuses a new id beside --resume"
    metrics = json.loads((resumed.header.session_dir / "metrics.json").read_text())
    assert metrics["claude_session_id"] == sid
    prompts = [e["text"] for e in resumed.events if e.get("event") == "prompt_received"]
    assert prompts == ["@recall"], "the earlier run's turn is the earlier log's"


@pytest.mark.parametrize(
    ("resume", "named"),
    [
        (
            "0b1c2d3e-4f50-4a61-8b72-93a4b5c6d7e8",
            "no conversation 0b1c2d3e-4f50-4a61-8b72-93a4b5c6d7e8",
        ),
        ("not-a-uuid", "not-a-uuid"),
    ],
    ids=["unknown-conversation", "not-a-uuid"],
)
def test_e2e_a_resume_of_no_conversation_is_refused_before_anything_starts(
    tmp_project, tmp_path, resume: str, named: str
) -> None:
    stub = install(tmp_path)

    with pytest.raises(SessionEnded) as refused:
        HeadlessSession.start(
            _argv(tmp_project, "--resume", resume, "hello"),
            cwd=tmp_project.path,
            env=session_env(stub, tmp_project),
        )

    assert refused.value.exit.code == 2
    assert refused.value.exit.events == (), "no header, no log: nothing started"
    assert named in refused.value.exit.stderr
    assert stub.argvs() == [], "the binary was never launched"
