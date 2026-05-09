"""E2E baseline for `ai-hats` (bare) — regression catcher for HATS-269.

Bare already migrated in HATS-267 (goes through human.yaml). These tests
lock the public behaviour so the post-migration code in HATS-269 (which
refactors _launch_session to use PipelineHarness) doesn't drift.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from ai_hats.cli import main



def test_human_no_role(project_dir: Path, mock_runners):
    res = CliRunner().invoke(main, [])

    assert res.exit_code == 0, res.output
    # Routed to WrapRunner
    assert len(mock_runners["wrap_calls"]) == 1
    call = mock_runners["wrap_calls"][0]
    assert call["role_override"] is None
    assert call["provider"] == "claude"
    assert call["extra_args"] == []
    # NB: session-review spawn is NOT done by the pipeline anymore — the
    # session_end auto-retro hook + auto_retro.py policy decide whether
    # to spawn (single source of truth, threshold-aware).


def test_human_with_role(project_dir: Path, mock_runners):
    res = CliRunner().invoke(main, ["--role", "assistant"])

    assert res.exit_code == 0, res.output
    call = mock_runners["wrap_calls"][0]
    assert call["role_override"] == "assistant"
    # system_prompt_override should be the composed role text (non-empty)
    sp = call.get("system_prompt_override")
    assert sp is not None and len(sp) > 0


def test_human_creates_session_artifacts(project_dir: Path, mock_runners):
    """Side-effect: session_dir + trace + metrics created."""
    res = CliRunner().invoke(main, [])
    assert res.exit_code == 0

    session_dirs = list((project_dir / ".gitlog").glob("session_*"))
    assert len(session_dirs) == 1
    sd = session_dirs[0]
    assert (sd / "trace.log").exists()
    assert (sd / "metrics.json").exists()
