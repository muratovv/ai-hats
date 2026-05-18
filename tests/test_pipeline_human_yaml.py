"""End-to-end test: ``human.yaml`` runs through loader and produces the
same flat-key state that downstream consumers expect.

Provider spawn is mocked at the runner level — we assert the pipeline
threads state correctly through compose_role → pre_log → launch_provider
→ spawn_session_review → post_log without exercising real PTY.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


from ai_hats.pipeline.loader import load_pipeline
from ai_hats.pipeline.pipeline import run as run_pipeline


_BUILTIN = (
    Path(__file__).parent.parent
    / "library/core/pipelines/human.yaml"
)


def _fake_session(tmp_path: Path) -> MagicMock:
    sess = MagicMock()
    sess.session_id = "20260101-010101-1"
    sess.session_dir = tmp_path / "session"
    sess.session_dir.mkdir(parents=True, exist_ok=True)
    sess.trace_path = sess.session_dir / "trace.log"
    sess.trace_path.write_text("(empty)")
    sess.metrics_path = sess.session_dir / "metrics.json"
    return sess


def test_human_pipeline_e2e(tmp_path: Path):
    pipeline = load_pipeline(_BUILTIN)

    fake_session = _fake_session(tmp_path)
    fake_runner = MagicMock()
    fake_runner.run.return_value = (0, fake_session)

    fake_assembler = MagicMock()
    fake_assembler.composer.compose.return_value = MagicMock(
        errors=[], merged_injection="ROLE PROMPT",
    )

    fake_proc = MagicMock(pid=42)

    with patch(
        "ai_hats.runtime.WrapRunner", return_value=fake_runner,
    ), patch(
        "ai_hats.assembler.Assembler", return_value=fake_assembler,
    ), patch(
        "ai_hats.models.ProjectConfig.from_yaml",
        return_value=MagicMock(provider="claude"),
    ), patch(
        "subprocess.Popen", return_value=fake_proc,
    ):
        final = run_pipeline(pipeline, {
            "role": "assistant",
            "interactive": True,
            "project_dir": tmp_path,
        })

    assert final["session_id"] == "20260101-010101-1"
    assert final["session_dir"] == fake_session.session_dir
    assert final["transcript_path"] == fake_session.trace_path
    assert final["exit_code"] == 0
    assert final["system_prompt"] == "ROLE PROMPT"
    # spawn_session_review removed from human.yaml — session-review is now
    # decided by the session_end auto-retro hook (threshold-aware), not
    # forced by the pipeline.
    assert "review_pid" not in final

    fake_assembler.composer.compose.assert_called_once_with("assistant")
    fake_runner.run.assert_called_once()
    call_kwargs = fake_runner.run.call_args.kwargs
    assert call_kwargs["role_override"] == "assistant"
    assert call_kwargs["system_prompt_override"] == "ROLE PROMPT"


def test_human_pipeline_e2e_no_role(tmp_path: Path):
    """role=None falls through compose_role to empty system_prompt."""
    pipeline = load_pipeline(_BUILTIN)

    fake_session = _fake_session(tmp_path)
    fake_runner = MagicMock()
    fake_runner.run.return_value = (0, fake_session)

    with patch(
        "ai_hats.runtime.WrapRunner", return_value=fake_runner,
    ), patch(
        "ai_hats.models.ProjectConfig.from_yaml",
        return_value=MagicMock(provider="claude"),
    ), patch("subprocess.Popen", return_value=MagicMock(pid=1)):
        final = run_pipeline(pipeline, {
            "role": None,
            "interactive": True,
            "project_dir": tmp_path,
        })

    assert final["system_prompt"] == ""
    assert final["exit_code"] == 0
    fake_runner.run.assert_called_once()
    assert fake_runner.run.call_args.kwargs["role_override"] is None
