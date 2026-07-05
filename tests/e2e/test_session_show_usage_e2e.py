"""e2e: ``ai-hats session show`` renders the Usage section from usage.json.

``dev_rule_e2e_gate`` coverage for the HATS-734 ``cli/session.py`` change.
Spawns the REAL ``ai-hats`` binary (no ``CliRunner``, no monkeypatch) against a
session dir carrying a ``usage.json``, and asserts the Usage section + the
``usage.json`` artifact appear in stdout.

Fail-under-revert: remove the ``_render_usage(s)`` call (or drop ``usage.json``
from the artifacts tuple) in ``cli/session.py`` → the asserted markers vanish →
this test fails. That is the exact contract the gate requires: the test
exercises the new behaviour through the real command chain, not alongside it.
"""

from __future__ import annotations

import json

import pytest
from ai_hats.paths import METRICS_JSON, USAGE_JSON, session_dirname

# smoke: also run by the merge-to-master CI gate (HATS-783)
pytestmark = [pytest.mark.integration, pytest.mark.smoke]

_USAGE = {
    "schema_version": "usage/v1",
    "always_on": {
        "first_cache_creation_input_tokens": 18204,
        "static": {"role": "maintainer", "total_tokens": 17000},
    },
    "aggregates": {
        "skill_loads": {"backlog-manager": 1},
        "tool_calls": 16,
        "tool_errors": 4,
        "tool_success_rate": 0.75,
    },
    "sidechain": {"is_sidechain": False},
    "flags": [],
}


def test_session_show_renders_usage_section(tmp_project):
    sid = "20260605-100000-1"
    sdir = tmp_project.agent_dir / "sessions" / "runs" / session_dirname(sid)
    sdir.mkdir(parents=True)
    (sdir / METRICS_JSON).write_text(
        json.dumps({
            "role": "maintainer", "provider": "claude", "exit_code": 0,
            "turns": 4, "tool_calls": 16,
        })
    )
    (sdir / USAGE_JSON).write_text(json.dumps(_USAGE))

    result = tmp_project.run("session", "show", sid)
    result.expect_ok()
    result.expect_stdout_contains(
        "Usage",
        "always_on (measured)",
        "18,204",
        "backlog-manager x1",
        USAGE_JSON,
    )
