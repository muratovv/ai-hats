"""e2e (HATS-2029)

flow:   the surface binary starts saying something the holder was never taught
        — a new kind of wire line, or a line that is not JSON at all
cmds:
    ai-hats headless -p claude -r assistant
expect: each such line is a notice in the log (unsupported_record, naming what
        came), the turn still ends with turn_ended, the next turn runs, and the
        session exits 0
why:    the wire is undocumented and drifts between binary versions; a client
        must see the drift in the log and never hang on it
"""

from __future__ import annotations

import pytest

from ai_hats_client import HeadlessSession
from ai_hats_client.testing import install

from _helpers.headless import session_env

pytestmark = [pytest.mark.integration, pytest.mark.surfaces]


def test_e2e_a_line_the_holder_cannot_read_is_a_notice_and_the_session_goes_on(
    tmp_project, tmp_path
) -> None:
    stub = install(tmp_path)
    with HeadlessSession.start(
        [str(tmp_project.ai_hats_binary), "headless", "-p", "claude", "-r", "assistant"],
        cwd=tmp_project.path,
        env=session_env(stub, tmp_project),
    ) as session:
        drifted = session.turn("@drift")
        after = session.turn("still here")
        end = session.close()

    drift = drifted.signals("unsupported_record")
    assert sorted(s["raw_code"] for s in drift) == ["future_line", "malformed-json"]
    assert {s["source"] for s in drift} == {"claude/wire", "headless"}
    assert drifted.ok and drifted.text == "ok: drift"
    # POSITIVE CONTROL: an ordinary turn says nothing of the kind
    assert after.signals("unsupported_record") == () and after.text == "ok: still here"
    assert end.code == 0
