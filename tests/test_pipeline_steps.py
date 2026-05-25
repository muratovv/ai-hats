"""Unit tests for built-in pipeline steps (HATS-267).

Each step is exercised in isolation with mocked source-functions where
the step delegates into existing runtime/composer/runner code.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ai_hats.pipeline.steps.compose import ComposeRole
from ai_hats.pipeline.steps.extract import ExtractMarker
from ai_hats.pipeline.steps.handoff import BuildHandoff
from ai_hats.pipeline.steps.log import PostLog, PreLog
from ai_hats.pipeline.steps.prompt import ResolvePrompt
from ai_hats.pipeline.steps.save import SaveArtifact
from ai_hats.pipeline.steps.session_review import RunSessionReview
from ai_hats.pipeline.steps.spawn_review import SpawnSessionReview
from ai_hats.paths import runs_dir


# ---------------- compose_role ----------------

def test_compose_role_omits_key_when_role_none(tmp_path: Path):
    """HATS-452 / П3: when no role is requested, the step OMITS the
    ``system_prompt`` key entirely (rather than emitting ``""``). The
    pipeline funnel + downstream consumers (``LaunchProvider`` →
    ``WrapRunner``) treat missing == None == "no override".
    """
    step = ComposeRole()
    out = step.run(project_dir=tmp_path, role=None)
    assert out == {}, (
        "compose_role must NOT emit system_prompt='' for missing role "
        "(HATS-452); the empty-string-as-absent trap broke composition "
        "delivery in WrapRunner."
    )


def test_compose_role_routes_through_facade(tmp_path: Path):
    """HATS-501: ``ComposeRole`` MUST route through the
    ``compose_for_role`` facade (HATS-456 single-derivation-point
    invariant) — bypassing it is the HATS-501 bug.

    Structural-only check: both ``Assembler`` and ``compose_for_role``
    are mocked, so this test asserts *which function is called* with
    *which arguments*, not that overlays actually land in the result.
    The real layered-composition assertion lives in
    ``tests/pipeline/test_compose_overlay_propagation.py``.
    """
    step = ComposeRole()
    fake_result = MagicMock(errors=[], merged_injection="ROLE PROMPT")
    fake_assembler = MagicMock()
    with patch("ai_hats.assembler.Assembler", return_value=fake_assembler), \
         patch("ai_hats.materialize.compose_for_role",
               return_value=fake_result) as facade:
        out = step.run(project_dir=tmp_path, role="judge")
    assert out == {"system_prompt": "ROLE PROMPT"}
    facade.assert_called_once_with(fake_assembler, "judge")


def test_compose_role_raises_on_compose_errors(tmp_path: Path):
    step = ComposeRole()
    fake_result = MagicMock(errors=["role not found"])
    with patch("ai_hats.assembler.Assembler", return_value=MagicMock()), \
         patch("ai_hats.materialize.compose_for_role", return_value=fake_result):
        with pytest.raises(RuntimeError, match="failed to resolve role"):
            step.run(project_dir=tmp_path, role="ghost")


# ---------------- resolve_prompt ----------------

def test_resolve_prompt_reads_path(tmp_path: Path):
    f = tmp_path / "p.txt"
    f.write_text("hello prompt")
    out = ResolvePrompt().run(prompt_path=f)
    assert out == {"prompt_text": "hello prompt"}


def test_resolve_prompt_default_text():
    out = ResolvePrompt({"default_text": "fallback"}).run(prompt_path=None)
    assert out == {"prompt_text": "fallback"}


def test_resolve_prompt_default_empty():
    out = ResolvePrompt().run()
    assert out == {"prompt_text": ""}


# ---------------- build_handoff ----------------

def test_build_handoff_delegates(tmp_path: Path):
    expected = tmp_path / "handoff.md"
    with patch("ai_hats.cli.reflect._build_handoff", return_value=expected) as m:
        out = BuildHandoff().run(project_dir=tmp_path)
    m.assert_called_once_with(tmp_path)
    assert out == {"handoff_path": expected}


# ---------------- pre_log / post_log ----------------

def test_pre_log_prints_known_keys(capsys):
    step = PreLog({"keys": ["a", "b"]})
    out = step.run(a=1, b="hi")
    assert out == {}
    err = capsys.readouterr().err
    assert "pre_log fires" in err
    assert "a = 1" in err
    assert "b = 'hi'" in err


def test_post_log_skips_missing_keys(capsys):
    step = PostLog({"keys": ["x", "y"]})
    step.run(x=42)
    err = capsys.readouterr().err
    assert "x = 42" in err
    assert "y" not in err  # missing → silently skipped


def test_log_keys_must_be_str_list():
    with pytest.raises(ValueError, match="must be list"):
        PreLog({"keys": "not-a-list"})


def test_log_truncates_huge_values(capsys):
    """Generalized safety net: long state values get cut + marker."""
    huge = "x" * 5000
    PreLog({"keys": ["big"]}).run(big=huge)
    err = capsys.readouterr().err
    # Original 5000 chars NOT in output verbatim
    assert huge not in err
    # Truncation marker present
    assert "more chars]" in err
    # Some prefix is preserved
    assert "'xxxx" in err


def test_log_failure_policy_continue():
    assert PreLog().failure_policy == "continue"
    assert PostLog().failure_policy == "continue"


# ---------------- extract_marker ----------------

def test_extract_marker_happy(tmp_path: Path):
    f = tmp_path / "t.log"
    f.write_text("noise BEGIN_X content here END_X tail")
    step = ExtractMarker({"start": "BEGIN_X", "end": "END_X", "out_key": "got"})
    out = step.run(transcript_path=f)
    assert out == {"got": "content here"}


def test_extract_marker_missing_returns_empty(tmp_path: Path):
    f = tmp_path / "t.log"
    f.write_text("nothing relevant")
    step = ExtractMarker({"start": "AAA", "end": "BBB", "out_key": "got"})
    assert step.run(transcript_path=f) == {"got": ""}


def test_extract_marker_unreadable_returns_empty(tmp_path: Path):
    step = ExtractMarker({"start": "A", "end": "B", "out_key": "got"})
    assert step.run(transcript_path=tmp_path / "missing.log") == {"got": ""}


def test_extract_marker_validates_out_key():
    with pytest.raises(ValueError, match="not a valid identifier"):
        ExtractMarker({"start": "A", "end": "B", "out_key": "bad name"})


def test_extract_marker_missing_param():
    with pytest.raises(ValueError, match="missing param"):
        ExtractMarker({"start": "A"})


# ---------------- save_artifact ----------------

def test_save_artifact_writes_file(tmp_path: Path):
    template = str(tmp_path / "out" / "{ts}-x.txt")
    step = SaveArtifact({"key": "blob", "out_path_template": template})
    out = step.run(blob="payload")
    assert out["saved_path"].exists()
    assert out["saved_path"].read_text() == "payload"


def test_save_artifact_empty_content_legitimate(tmp_path: Path):
    template = str(tmp_path / "{ts}.txt")
    step = SaveArtifact({"key": "blob", "out_path_template": template})
    out = step.run(blob="")
    assert out["saved_path"].read_text() == ""


def test_save_artifact_validates_params():
    with pytest.raises(ValueError, match="missing param"):
        SaveArtifact({"key": "x"})


def test_save_artifact_substitutes_state_keys(tmp_path: Path):
    """Path template can reference any state key in addition to ``{ts}``.

    Used by reflect-role pipeline (HATS-263) to embed ``{target_role}``
    in the output path.
    """
    template = str(tmp_path / "out" / "{target_role}-{ts}.md")
    step = SaveArtifact({"key": "blob", "out_path_template": template})
    out = step.run(blob="report", target_role="judge")
    assert out["saved_path"].exists()
    assert out["saved_path"].name.startswith("judge-")
    assert out["saved_path"].read_text() == "report"


def test_save_artifact_ts_only_regression(tmp_path: Path):
    """Templates with only ``{ts}`` keep working when extra state is passed."""
    template = str(tmp_path / "{ts}.txt")
    step = SaveArtifact({"key": "blob", "out_path_template": template})
    out = step.run(blob="x", role="ignored", project_dir=tmp_path)
    assert out["saved_path"].read_text() == "x"


def test_save_artifact_declares_template_keys_as_requires(tmp_path: Path):
    """Placeholders in the template surface in ``io.requires`` so the
    pipeline core projects them through to ``run`` (HATS-263)."""
    template = str(tmp_path / "{target_role}-{ts}.md")
    step = SaveArtifact({"key": "blob", "out_path_template": template})
    assert "target_role" in step.io.requires
    assert "blob" in step.io.requires
    assert "ts" not in step.io.requires  # ts is generated inside run()


def test_save_artifact_ts_only_no_extra_requires(tmp_path: Path):
    """``{ts}``-only templates declare only the data key as required."""
    template = str(tmp_path / "{ts}.txt")
    step = SaveArtifact({"key": "blob", "out_path_template": template})
    assert step.io.requires == frozenset({"blob"})


def test_save_artifact_expands_ai_hats_dir_placeholder(tmp_path: Path):
    """HATS-395 regression. ``<ai_hats_dir>`` in ``out_path_template`` must be
    expanded to the project's ai-hats dir; the literal placeholder must not
    survive into the filesystem path (which would create a bogus
    ``<ai_hats_dir>/`` directory in the project root — HATS-380 recurrence).
    """
    template = "<ai_hats_dir>/sessions/retros/judge/{ts}-report.md"
    step = SaveArtifact({"key": "blob", "out_path_template": template})
    # Step auto-requires ``project_dir`` when the placeholder is present
    # so the pipeline core surfaces a missing-projection at build time
    # instead of silently writing to a literal-placeholder path.
    assert "project_dir" in step.io.requires
    out = step.run(blob="payload", project_dir=tmp_path)
    saved = out["saved_path"]
    assert "<ai_hats_dir>" not in str(saved)
    assert ".agent/ai-hats/sessions/retros/judge/" in str(saved).replace("\\", "/")
    assert saved.exists()
    assert saved.read_text() == "payload"


def test_save_artifact_template_without_placeholder_skips_project_dir(tmp_path: Path):
    """Backwards-compat (HATS-395): templates that don't embed ``<ai_hats_dir>``
    must NOT require ``project_dir`` — the pipeline-state contract stays
    minimal for the common case (e.g. reflect-role's ``{target_role}``-only
    template).
    """
    template = str(tmp_path / "{ts}.txt")
    step = SaveArtifact({"key": "blob", "out_path_template": template})
    assert "project_dir" not in step.io.requires
    out = step.run(blob="x")  # no project_dir argument required
    assert out["saved_path"].read_text() == "x"


# ---------------- spawn_session_review ----------------

def test_spawn_session_review_returns_pid(tmp_path: Path):
    fake_proc = MagicMock(pid=12345)
    with patch("subprocess.Popen", return_value=fake_proc) as m:
        out = SpawnSessionReview({"max_retries": 2}).run(
            session_id="20260101-010101-1", project_dir=tmp_path,
        )
    assert out == {"review_pid": 12345}
    cmd = m.call_args[0][0]
    assert "ai_hats.cli.reflect_session_main" in cmd
    assert "2" in cmd  # max_retries
    log_path = (
        runs_dir(tmp_path) / "session_20260101-010101-1" / "retro.log"
    )
    assert log_path.parent.exists()


def test_spawn_session_review_failure_policy_continue():
    assert SpawnSessionReview().failure_policy == "continue"


# ---------------- run_session_review ----------------

def test_run_session_review_delegates(tmp_path: Path):
    expected = tmp_path / "review.md"
    fake_runner = MagicMock()
    fake_runner.run.return_value = expected
    with patch(
        "ai_hats.retro.session_review_runner.SessionReviewRunner",
        return_value=fake_runner,
    ):
        out = RunSessionReview({"max_retries": 3}).run(
            session_id="sid", project_dir=tmp_path,
        )
    assert out == {"review_path": expected}
    fake_runner.run.assert_called_once_with(
        "sid", max_retries=3, harness_policy=None,
    )


def test_run_session_review_failure_policy_halt():
    assert RunSessionReview().failure_policy == "halt"
