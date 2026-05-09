"""End-to-end tests for ``execute_pipeline`` preset (Phase 1).

Asserts bit-equivalence with calling ``_do_execute`` directly: the preset
funnels its inputs into ``_do_execute`` and lifts ``session``/``exit_code``
back into pipeline state without altering the call.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ai_hats.pipeline import run
from ai_hats.pipeline.presets import execute_pipeline


def test_execute_preset_interactive_threads_exit_code(monkeypatch) -> None:
    captured: dict = {}

    def fake_do_execute(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr("ai_hats.cli.execute._do_execute", fake_do_execute)

    state = run(
        execute_pipeline,
        {
            "interactive": True,
            "role": "assistant",
            "prompt_text": None,
            "extra_args": [],
        },
    )
    assert state["exit_code"] == 0
    assert state["session"] is None
    # _do_execute received the projected kwargs — confirms LaunchProvider
    # forwards optional state correctly.
    assert captured["interactive"] is True
    assert captured["role"] == "assistant"
    assert captured["prompt"] is None
    assert captured["extra_args"] == []


def test_execute_preset_batch_threads_session(
    monkeypatch, tmp_path: Path
) -> None:
    metrics_path = tmp_path / "metrics.json"
    metrics_path.write_text(json.dumps({"exit_code": 0}))
    fake_session = SimpleNamespace(metrics_path=metrics_path)

    def fake_do_execute(**_: Any) -> Any:
        return fake_session

    monkeypatch.setattr("ai_hats.cli.execute._do_execute", fake_do_execute)

    state = run(
        execute_pipeline,
        {
            "interactive": False,
            "role": "assistant",
            "prompt_text": "ping",
            "ticket": "HATS-265",
        },
    )
    assert state["exit_code"] == 0
    assert state["session"] is fake_session


def test_execute_preset_batch_handles_missing_metrics(
    monkeypatch, tmp_path: Path
) -> None:
    fake_session = SimpleNamespace(metrics_path=tmp_path / "absent.json")

    def fake_do_execute(**_: Any) -> Any:
        return fake_session

    monkeypatch.setattr("ai_hats.cli.execute._do_execute", fake_do_execute)

    state = run(execute_pipeline, {"interactive": False, "role": "assistant"})
    assert state["session"] is fake_session
    assert state["exit_code"] == 1  # default when metrics absent


def test_execute_pipeline_io_shape() -> None:
    """Lock the public contract of the preset for downstream callers."""
    io = execute_pipeline.io
    assert io.requires == frozenset({"interactive"})
    assert io.produces == frozenset({"session", "exit_code"})
    assert "role" in io.optional
    assert "prompt_text" in io.optional


@pytest.mark.parametrize("interactive,expected_session", [(True, None)])
def test_execute_preset_session_is_none_for_interactive(
    monkeypatch, interactive: bool, expected_session: Any
) -> None:
    monkeypatch.setattr(
        "ai_hats.cli.execute._do_execute", lambda **_: 0
    )
    state = run(execute_pipeline, {"interactive": interactive})
    assert state["session"] is expected_session


# ---------- shape lock-in ----------


def test_execute_pipeline_canonical_skeleton() -> None:
    """``execute_pipeline`` is ``[PreLogStub, LaunchProvider, PostLogStub]``.

    Phase 2/3 will replace the log-stubs with real pre/post chains — the
    middle step (LaunchProvider) will then shrink to spawn+wait.
    """
    assert execute_pipeline.pipeline_name == "execute"
    assert len(execute_pipeline.steps) == 3
    assert execute_pipeline.steps[0].io.name == "pre_log_stub"
    assert execute_pipeline.steps[1].io.name == "launch_provider"
    assert execute_pipeline.steps[2].io.name == "post_log_stub"


def test_execute_preset_log_stubs_print_to_stderr(monkeypatch, capsys) -> None:
    """Smoke: PreLogStub/PostLogStub print on a live execute_pipeline run."""
    monkeypatch.setattr("ai_hats.cli.execute._do_execute", lambda **_: 0)

    run(execute_pipeline, {
        "interactive": True,
        "role": "assistant",
        "prompt_text": "ping",
    })

    err = capsys.readouterr().err
    assert "pre_log_stub  fires" in err
    assert "post_log_stub fires" in err
    assert "in.role = 'assistant'" in err
    assert "in.exit_code = 0" in err
