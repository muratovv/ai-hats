"""Migration step 8: retire the fabricated token zeros in agy records (HATS-1397).

agy emits no usage field at all, so every `tokens` block ever written for it is a
placeholder. Until this step, those zeros read as a measurement: on the real
archive **119 of 237 agy sessions** answered `is_zero_output` with True — one of
them a 55-turn session — because `tokens.output == 0 and tool_calls == 0` looked
like proof of silence.

`session backfill` cannot repair them: agy never receives a provider session id,
so all 237 fail its attribution guard. The repair needs no transcript, because it
re-derives nothing — it withdraws a claim the surface could never have made.
"""

from __future__ import annotations

import json
from pathlib import Path

from ai_hats.assembler import Assembler
from ai_hats.harness.diagnostic import is_zero_output
from ai_hats.migrations import run_pending
from ai_hats.paths import PROJECT_CONFIG
from ai_hats_observe.artifacts import FLAG_NO_TOKEN_TELEMETRY, METRICS_JSON
from ai_hats_core.layout import ProjectLayout

ZEROS = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}


def _project(tmp_path: Path) -> Path:
    (tmp_path / PROJECT_CONFIG).write_text(
        "schema_version: 4\nprovider: agy\nai_hats_dir: .agent/ai-hats\nmigration_step: 7\n"
    )
    return tmp_path


def _session(project_dir: Path, name: str, metrics: dict) -> Path:
    d = ProjectLayout.at(project_dir).sessions.runs / f"session_{name}"
    d.mkdir(parents=True)
    path = d / METRICS_JSON
    path.write_text(json.dumps(metrics))
    return path


def _read(path: Path) -> dict:
    return json.loads(path.read_text())


def test_a_productive_agy_session_stops_reading_as_silent(tmp_path):
    """The archive's real shape: turns recorded, tools unparsed, tokens fabricated."""
    project = _project(tmp_path)
    path = _session(
        project,
        "20260722-082353-1",
        {
            "provider": "agy",
            "role": "maintainer",
            "exit_code": 0,
            "turns": 55,
            "tool_calls": 0,
            "tokens": dict(ZEROS),
            "models": {},
        },
    )
    assert is_zero_output(_read(path)) is True, "precondition: the record lies today"

    run_pending(Assembler(project))

    after = _read(path)
    assert "tokens" not in after, "the placeholder zeros are still asserted"
    assert FLAG_NO_TOKEN_TELEMETRY in after["flags"]
    assert after["turns"] == 55, "the counters agy DID measure must survive"
    assert is_zero_output(after) is False


def test_records_of_other_surfaces_are_untouched(tmp_path):
    """A claude zero is a measurement — only agy's is a placeholder."""
    project = _project(tmp_path)
    path = _session(
        project,
        "20260722-090000-1",
        {
            "provider": "claude",
            "measured": True,
            "turns": 3,
            "tool_calls": 0,
            "tokens": dict(ZEROS),
        },
    )

    run_pending(Assembler(project))

    assert _read(path)["tokens"] == ZEROS


def test_the_step_is_idempotent_and_keeps_prior_flags(tmp_path):
    project = _project(tmp_path)
    path = _session(
        project,
        "20260722-091000-1",
        {
            "provider": "agy",
            "turns": 2,
            "tool_calls": 0,
            "tokens": dict(ZEROS),
            "flags": ["not-finalized"],
        },
    )

    run_pending(Assembler(project))
    first = _read(path)
    run_pending(Assembler(project))

    assert _read(path) == first
    assert first["flags"] == ["not-finalized", FLAG_NO_TOKEN_TELEMETRY]
