"""e2e (HATS-2020)

flow:   a user starts a headless session with the parameters they already use
        for a bare ai-hats session — a model, a tag, a first prompt, provider
        flags — and with one of the flags the holder keeps for itself
cmds:
    ai-hats headless -p claude -r assistant -m claude-haiku-4-5 --tag k=v "first turn"
    ai-hats headless -p claude -r assistant --resume abc  # no-resolve: refused by design, the holder owns --resume
expect: the model and the provider flags reach the binary, the tag lands in
        metrics.json, and the positional prompt is the first turn; a flag
        that makes the wire (--resume, --permission-prompt-tool, …) or a
        surface with no wire is refused with exit 2 before anything starts —
        no header, no session
why:    headless is the same session as bare ai-hats with another channel, so
        its parameters must mean the same; the wire is the holder's, and a
        flag that changes it would break the contract silently
"""

from __future__ import annotations

import json

import pytest

from ai_hats_client import HeadlessSession, SessionEnded
from ai_hats_client.testing import install

from _helpers.headless import session_env

pytestmark = [pytest.mark.integration, pytest.mark.surfaces]


def _argv(project, *args: str) -> list[str]:
    return [str(project.ai_hats_binary), "headless", "-r", "assistant", *args]


def test_e2e_the_parameters_of_a_bare_session_mean_the_same_here(tmp_project, tmp_path) -> None:
    stub = install(tmp_path)
    with HeadlessSession.start(
        _argv(
            tmp_project,
            "-p",
            "claude",
            "-m",
            "claude-haiku-4-5",
            "--tag",
            "k=v",
            "first turn",
            "--add-dir",
            str(tmp_path),
        ),
        cwd=tmp_project.path,
        env=session_env(stub, tmp_project),
    ) as session:
        first = session.next_turn()
        end = session.close()

    assert first.text == "ok: first turn", "the positional prompt is the first turn"
    assert end.code == 0
    (argv,) = stub.argvs()
    assert argv[argv.index("--model") + 1] == "claude-haiku-4-5"
    assert argv[argv.index("--add-dir") + 1] == str(tmp_path), "provider flags pass through"
    assert "first turn" not in argv, "the first turn goes over the wire, not argv"
    metrics = json.loads((session.header.session_dir / "metrics.json").read_text())
    assert metrics["tags"] == {"k": "v"}


@pytest.mark.parametrize(
    ("args", "named"),
    [
        (("-p", "claude", "--resume", "abc"), "--resume"),
        (("-p", "claude", "--permission-prompt-tool", "stdio"), "--permission-prompt-tool"),
        (("-p", "claude", "--output-format=text"), "--output-format"),
        (("-p", "claude", "--no-session-persistence"), "--no-session-persistence"),
        (
            (
                "-p",
                "codex",
            ),
            "codex",
        ),
    ],
)
def test_e2e_what_would_break_the_wire_is_refused_before_anything_starts(
    tmp_project, tmp_path, args: tuple[str, ...], named: str
) -> None:
    stub = install(tmp_path)

    with pytest.raises(SessionEnded) as refused:
        HeadlessSession.start(
            _argv(tmp_project, *args), cwd=tmp_project.path, env=session_env(stub, tmp_project)
        )

    assert refused.value.exit.code == 2
    assert refused.value.exit.events == (), "no header, no log: nothing started"
    assert named in refused.value.exit.stderr
    assert stub.argvs() == [], "the binary was never launched"
