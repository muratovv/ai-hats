"""e2e (HATS-2027)

flow:   a script runs a role's session headless under policy=always, and the
        session reaches the session reviewer as a TUI one does
cmds:
    ai-hats headless -p claude -r assistant
expect: retro.log holds the finalize's decision to run the reviewer and, with
        HATS_SKIP_RETRO=1 standing in for it, a suppressed-by-guard outcome
why:    a session the reviewer never sees is invisible to the reflect loop
"""

from __future__ import annotations

import pytest
import yaml

from _helpers.headless_client import HeadlessSession
from _helpers.stub_claude import install
from ai_hats.constants import ENV_SKIP_RETRO

pytestmark = [pytest.mark.integration, pytest.mark.surfaces]


def _retro_lines(session_dir) -> list[tuple[str, str, str]]:
    """``(source, action, detail)`` of every line the session's retro.log holds."""
    log = session_dir / "retro.log"
    assert log.exists(), "the finalize left no retro.log: no retro decision was made"
    return [tuple(line.split("\t")[1:4]) for line in log.read_text().splitlines()]


def test_e2e_a_headless_session_reaches_the_session_reviewer(tmp_project, tmp_path) -> None:
    config = yaml.safe_load(tmp_project.yaml.read_text())
    config.setdefault("feedback", {})["session_retro"] = {"policy": "always"}
    tmp_project.yaml.write_text(yaml.safe_dump(config))
    env = install(tmp_path).session_env(tmp_project)
    env[ENV_SKIP_RETRO] = "1"

    session = HeadlessSession.start(
        [str(tmp_project.ai_hats_binary), "headless", "-p", "claude", "-r", "assistant"],
        cwd=tmp_project.path,
        env=env,
    )
    session.turn("first")
    end = session.close()

    assert end.code == 0
    lines = _retro_lines(session.header.session_dir)
    assert ("runtime", "decision", "run: policy=always") in lines, lines
    outcomes = [detail for source, action, detail in lines if action == "outcome"]
    assert len(outcomes) == 1 and outcomes[0].startswith("suppressed-by-guard"), lines
