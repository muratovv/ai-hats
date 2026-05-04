"""Tests for the reflect-session auto-trigger gate in auto_retro.

Gate logic (HATS-210):
  - decision=run + builder_mode=LLM → spawn reflect-session
  - decision=run + builder_mode=PROGRAMMATIC → do NOT spawn
  - decision=skip/hint → do NOT spawn
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import yaml


def _project(tmp_path: Path) -> Path:
    pd = tmp_path / "proj"
    (pd / ".gitlog").mkdir(parents=True)
    (pd / ".agent" / "hypotheses").mkdir(parents=True)
    (pd / ".agent" / "backlog" / "proposals").mkdir(parents=True)
    return pd


def _write_config(pd: Path, *, mode: str = "llm", policy: str = "always"):
    cfg = {
        "feedback": {
            "session_retro": {
                "policy": policy,
                "mode": mode,
                "background": False,
                "smart_threshold": {"min_turns": 1, "min_tool_calls": 1},
            }
        }
    }
    (pd / "ai-hats.yaml").write_text(yaml.safe_dump(cfg))


def _make_session_metrics(pd: Path, session_id: str, *, turns=5, tool_calls=10):
    sdir = pd / ".gitlog" / f"session_{session_id}"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "metrics.json").write_text(json.dumps({
        "role": "test", "turns": turns, "tool_calls": tool_calls,
    }))


class _SpawnRecorder:
    """Stand-in for subprocess.Popen — records calls, returns dummy proc."""
    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, args, *posargs, **kwargs):
        self.calls.append(list(args))
        class _Dummy:
            pid = 99999
        return _Dummy()


def test_llm_mode_run_decision_spawns_reflect_session(tmp_path: Path):
    pd = _project(tmp_path)
    _write_config(pd, mode="llm")
    _make_session_metrics(pd, "s1")

    from ai_hats.retro import auto_retro

    rec = _SpawnRecorder()
    with patch("ai_hats.retro.builder.SessionRetroBuilder") as MockBuilder, \
         patch("subprocess.Popen", rec):
        instance = MockBuilder.return_value
        instance.build_and_save.return_value = pd / "fake.md"

        auto_retro._run_foreground(pd, "s1", "llm")

    # We expect exactly one Popen call — the reflect-session spawn.
    assert len(rec.calls) == 1, rec.calls
    cmd = rec.calls[0]
    # python -m ai_hats.cli.reflect_session_main <sid> 1
    assert "ai_hats.cli.reflect_session_main" in cmd
    assert "s1" in cmd


def test_programmatic_mode_does_not_spawn(tmp_path: Path):
    pd = _project(tmp_path)
    _write_config(pd, mode="programmatic")
    _make_session_metrics(pd, "s1")

    from ai_hats.retro import auto_retro

    rec = _SpawnRecorder()
    with patch("ai_hats.retro.builder.SessionRetroBuilder") as MockBuilder, \
         patch("subprocess.Popen", rec):
        instance = MockBuilder.return_value
        instance.build_and_save.return_value = pd / "fake.md"

        auto_retro._run_foreground(pd, "s1", "programmatic")

    assert rec.calls == [], "PROGRAMMATIC builder must NOT spawn reflect-session"


def test_skip_decision_does_not_call_run_foreground(tmp_path: Path):
    """Verifies the policy gate at should_run level — not _run_foreground."""
    pd = _project(tmp_path)
    _write_config(pd, mode="llm", policy="off")
    _make_session_metrics(pd, "s1")

    from ai_hats.retro import auto_retro
    action, reason = auto_retro.should_run(
        pd / "ai-hats.yaml",
        pd / ".gitlog" / "session_s1" / "metrics.json",
    )
    assert action == "skip"


def test_smart_below_threshold_skips(tmp_path: Path):
    pd = _project(tmp_path)
    _write_config(pd, mode="llm", policy="smart")
    _make_session_metrics(pd, "s1", turns=0, tool_calls=0)

    from ai_hats.retro import auto_retro
    action, _ = auto_retro.should_run(
        pd / "ai-hats.yaml",
        pd / ".gitlog" / "session_s1" / "metrics.json",
    )
    assert action == "skip"
