"""End-to-end test: ``human.yaml`` runs through loader and produces the
same flat-key state that downstream consumers expect.

Provider spawn is mocked at the runner level — we assert the pipeline
threads state correctly through compose_role → provider → post steps
without exercising real PTY. HATS-865: the pipeline no longer composes;
the integrator seeds a ready ``composition`` payload into the initial
state, ``compose_role`` projects it, and ``provider`` hands the SAME
object to the runner (funnel/runner identity — ADR-0005 П1).
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

    payload = MagicMock(name="composition_payload")
    payload.result.merged_injection = "ROLE PROMPT"

    fake_proc = MagicMock(pid=42)

    with patch(
        "ai_hats.runtime.WrapRunner", return_value=fake_runner,
    ) as wrap_cls, patch(
        "subprocess.Popen", return_value=fake_proc,
    ):
        final = run_pipeline(pipeline, {
            "role": "assistant",
            "interactive": True,
            "project_dir": tmp_path,
            "composition": payload,
        })

    assert final["session_id"] == "20260101-010101-1"
    assert final["session_dir"] == fake_session.session_dir
    assert final["transcript_path"] == fake_session.trace_path
    assert final["exit_code"] == 0
    # compose_role is a projection of the seeded payload (HATS-865).
    assert final["system_prompt"] == "ROLE PROMPT"
    # spawn_session_review removed from human.yaml — session-review is now
    # decided by the session_end auto-retro hook (threshold-aware), not
    # forced by the pipeline.
    assert "review_pid" not in final

    # HATS-865 identity assert: the funnel-seeded payload object IS the
    # object the runner receives — no second composition anywhere in the
    # pipeline (ADR-0005 П1).
    wrap_cls.assert_called_once_with(tmp_path, payload)
    fake_runner.run.assert_called_once()
    call_kwargs = fake_runner.run.call_args.kwargs
    # HATS-452 (П2 in ADR-0005): WrapRunner has NO system_prompt_override
    # channel — composition reaches the agent via build_session_prompt.
    assert "system_prompt_override" not in call_kwargs


def test_human_pipeline_e2e_empty_injection_omits_system_prompt(tmp_path: Path):
    """A payload with an empty merged injection omits the funnel key
    entirely (HATS-452 П3: never emit ``""`` as absent)."""
    pipeline = load_pipeline(_BUILTIN)

    fake_session = _fake_session(tmp_path)
    fake_runner = MagicMock()
    fake_runner.run.return_value = (0, fake_session)

    payload = MagicMock(name="composition_payload")
    payload.result.merged_injection = ""

    with patch(
        "ai_hats.runtime.WrapRunner", return_value=fake_runner,
    ), patch("subprocess.Popen", return_value=MagicMock(pid=1)):
        final = run_pipeline(pipeline, {
            "role": None,
            "interactive": True,
            "project_dir": tmp_path,
            "composition": payload,
        })

    assert "system_prompt" not in final
    assert final["exit_code"] == 0
    fake_runner.run.assert_called_once()
