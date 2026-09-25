"""e2e (HATS-2029)

flow:   a headless session runs close to its quota, then into the wall
cmds:
    ai-hats headless -p claude -r assistant
expect: the warning is one approaching_limit notice in the log; the wall is one
        wait signal on the refused turn, carrying when the quota lifts, and the
        turn ends with ok false; the session exits 0
why:    only the wire says the quota is close, and a harness that sees the
        wall must know when to retry; each is said once, by one producer
"""

from __future__ import annotations

import pytest

from ai_hats_client import HeadlessSession
from ai_hats_client.testing import QUOTA_RESETS_AT, install

from _helpers.headless import session_env

pytestmark = [pytest.mark.integration, pytest.mark.surfaces]


def test_e2e_the_quota_warning_and_the_wall_are_each_said_once(tmp_project, tmp_path) -> None:
    stub = install(tmp_path)
    with HeadlessSession.start(
        [str(tmp_project.ai_hats_binary), "headless", "-p", "claude", "-r", "assistant"],
        cwd=tmp_project.path,
        env=session_env(stub, tmp_project),
    ) as session:
        warned = session.turn("@quota allowed_warning")
        walled = session.turn("@quota rejected")
        end = session.close()

    (warning,) = warned.signals("approaching_limit")
    assert warning["source"] == "claude/wire" and "five_hour" in warning["detail"]
    assert warned.ok
    (wall,) = walled.signals("wait")
    assert wall["retry_after"] == QUOTA_RESETS_AT
    assert not walled.ok
    # POSITIVE CONTROL: the warning belongs to its turn alone
    assert walled.signals("approaching_limit") == ()
    assert end.code == 0
