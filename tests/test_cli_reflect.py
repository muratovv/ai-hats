"""Tests for `ai-hats reflect` (HATS-201) and `judge-aggregate --interactive`."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

from ai_hats.cli import main
from ai_hats.retro.backfill import BackfillSummary


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _summary(*, total=0, saved=0, failed=0, dry_run=0, duration=0.0) -> BackfillSummary:
    s = BackfillSummary(total_candidates=total)
    s.saved = saved
    s.failed = failed
    s.dry_run = dry_run
    s.total_duration_s = duration
    return s


class _FakeSession:
    """Minimal stand-in for observe.Session — only the bits reflect uses."""
    def __init__(self, sid: str, *, has_metrics: bool = True, turns: int = 5):
        self.session_id = sid
        # Mimic real metrics_path semantics: empty path stays falsy via .exists().
        self._has = has_metrics
        self._turns = turns
        self._tool_calls = 10
        self.metrics_path = SimpleNamespace(
            exists=lambda: self._has,
            read_text=lambda: f'{{"turns":{self._turns},"tool_calls":{self._tool_calls},"role":"assistant"}}',  # noqa: E501
        )


def _patch_pipeline(
    monkeypatch,
    *,
    backfill_summary: BackfillSummary | None = None,
    sessions: list[_FakeSession] | None = None,
    reviewed: set[str] | None = None,
    bundle_id: str = "BUNDLE-2026-05-01-001",
    judge_path: Path | None = None,
    judge_raises: Exception | None = None,
    aggregate_path: Path | None = None,
    aggregate_raises: Exception | None = None,
):
    """Stub out every external collaborator reflect touches.

    Tests pass simple objects in; we wire them onto the import sites that
    `cli.reflect._stage_*` functions look them up from at call time.
    """
    summary = backfill_summary or _summary()
    sessions = sessions or []
    reviewed = reviewed or set()

    def fake_run_backfill(_pdir, *, mode, since, min_turns, force, dry_run, parallel, printer):
        return summary

    monkeypatch.setattr("ai_hats.retro.backfill.run_backfill", fake_run_backfill)

    class FakeSessionManager:
        def __init__(self, _pdir): pass
        def list_sessions(self, productive_only=True, last_n=None):
            return sessions

    monkeypatch.setattr("ai_hats.observe.SessionManager", FakeSessionManager)

    created_bundles: list[tuple[list[str], str | None]] = []

    class FakeBundleManager:
        def __init__(self, _pdir): pass
        def reviewed_session_ids(self):
            return reviewed
        def create(self, session_ids, *, notes=None):
            created_bundles.append((list(session_ids), notes))
            return SimpleNamespace(bundle_id=bundle_id, session_ids=list(session_ids))

    monkeypatch.setattr("ai_hats.retro.bundles.BundleManager", FakeBundleManager)

    judge_calls: list[dict] = []

    class FakeJudgeRunner:
        def __init__(self, _pdir): pass
        def judge(self, *, bundle_id, focus=None):
            judge_calls.append({"bundle_id": bundle_id, "focus": focus})
            if judge_raises is not None:
                raise judge_raises
            return judge_path or Path("/tmp/judge-fake.md")

    monkeypatch.setattr("ai_hats.retro.judge.JudgeRunner", FakeJudgeRunner)

    aggregate_calls: list[dict] = []

    class FakeAggregator:
        def __init__(self, _pdir): pass
        def aggregate(self, *, strategy, since, min_severity):
            aggregate_calls.append(
                {"strategy": strategy, "since": since, "min_severity": min_severity}
            )
            if aggregate_raises is not None:
                raise aggregate_raises
            return aggregate_path or Path("/tmp/agg-fake.md")

    monkeypatch.setattr("ai_hats.retro.aggregator.Aggregator", FakeAggregator)

    return SimpleNamespace(
        created_bundles=created_bundles,
        judge_calls=judge_calls,
        aggregate_calls=aggregate_calls,
    )


# --------------------------------------------------------------------------
# reflect — happy path
# --------------------------------------------------------------------------


class TestReflectHelp:
    def test_help_lists_flags(self):
        result = CliRunner().invoke(main, ["reflect", "--help"])
        assert result.exit_code == 0, result.output
        for flag in (
            "--since", "--min-turns", "--parallel", "--mode",
            "--focus", "--min-severity", "--interactive", "--dry-run",
        ):
            assert flag in result.output


class TestReflectFullPipeline:
    def test_full_pipeline_runs_all_four_stages(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".agent").mkdir()  # mark as project root for _project_dir
        spy = _patch_pipeline(
            monkeypatch,
            backfill_summary=_summary(total=2, saved=2, duration=1.5),
            sessions=[_FakeSession("20260501-100000-1"), _FakeSession("20260501-110000-2")],
            reviewed=set(),  # both unreviewed
            bundle_id="BUNDLE-2026-05-01-007",
            judge_path=Path("/tmp/judge-pipeline.md"),
            aggregate_path=Path("/tmp/agg-pipeline.md"),
        )

        result = CliRunner().invoke(main, ["reflect"])

        assert result.exit_code == 0, result.output
        assert len(spy.created_bundles) == 1
        assert spy.created_bundles[0][0] == ["20260501-100000-1", "20260501-110000-2"]
        assert spy.judge_calls == [
            {"bundle_id": "BUNDLE-2026-05-01-007", "focus": None}
        ]
        assert len(spy.aggregate_calls) == 1
        assert "Reflect summary" in result.output
        assert "BUNDLE-2026-05-01-007" in result.output

    def test_passes_focus_and_min_severity(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".agent").mkdir()
        spy = _patch_pipeline(
            monkeypatch,
            backfill_summary=_summary(),
            sessions=[_FakeSession("20260501-100000-1")],
        )

        result = CliRunner().invoke(main, [
            "reflect", "--focus", "reliability", "--min-severity", "high",
        ])

        assert result.exit_code == 0, result.output
        assert spy.judge_calls[0]["focus"] == "reliability"
        from ai_hats.retro.common import Severity
        assert spy.aggregate_calls[0]["min_severity"] == Severity.HIGH


class TestReflectAllSkipped:
    def test_nothing_to_do(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".agent").mkdir()
        _patch_pipeline(
            monkeypatch,
            backfill_summary=_summary(),
            sessions=[],
            aggregate_raises=ValueError("No judge retros found"),
        )

        result = CliRunner().invoke(main, ["reflect"])

        assert result.exit_code == 0, result.output
        assert "Nothing to do" in result.output

    def test_unreviewed_skipped_when_all_reviewed(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".agent").mkdir()
        spy = _patch_pipeline(
            monkeypatch,
            sessions=[_FakeSession("20260501-100000-1")],
            reviewed={"20260501-100000-1"},
            aggregate_path=Path("/tmp/agg-existing.md"),
        )

        result = CliRunner().invoke(main, ["reflect"])

        assert result.exit_code == 0, result.output
        assert spy.created_bundles == []
        assert spy.judge_calls == []
        assert len(spy.aggregate_calls) == 1  # still aggregates over old judge retros


class TestReflectDryRun:
    def test_dry_run_does_not_call_bundle_judge_aggregate(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".agent").mkdir()
        spy = _patch_pipeline(
            monkeypatch,
            backfill_summary=_summary(total=3, dry_run=3),
            sessions=[_FakeSession("20260501-100000-1")],
        )

        result = CliRunner().invoke(main, ["reflect", "--dry-run"])

        assert result.exit_code == 0, result.output
        assert "DRY-RUN" in result.output
        assert spy.created_bundles == []
        assert spy.judge_calls == []
        assert spy.aggregate_calls == []
        assert "would run: ai-hats judge --bundle" in result.output
        assert "would run: ai-hats judge-aggregate" in result.output


class TestReflectJudgeFailure:
    def test_judge_failure_exits_non_zero_and_skips_aggregate(
            self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".agent").mkdir()
        spy = _patch_pipeline(
            monkeypatch,
            sessions=[_FakeSession("20260501-100000-1")],
            judge_raises=RuntimeError("judge sub-agent crashed"),
        )

        result = CliRunner().invoke(main, ["reflect"])

        assert result.exit_code == 1, result.output
        assert "judge step failed" in result.output
        assert "judge sub-agent crashed" in result.output
        # Aggregate should still have been attempted on previous judge retros
        # so the user gets value from past runs even when *this* judge fails.
        assert len(spy.aggregate_calls) == 1


# --------------------------------------------------------------------------
# judge-aggregate --interactive
# --------------------------------------------------------------------------


class TestJudgeAggregateInteractive:
    def test_interactive_calls_handoff(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".agent").mkdir()
        agg_path = tmp_path / ".agent" / "retrospectives" / "aggregated" / "AGG-2026-05-01-001.md"
        agg_path.parent.mkdir(parents=True)
        agg_path.write_text("---\nschema: hats-aggregation/v1\n---\n# Body\n")

        class FakeAgg:
            def __init__(self, _pdir): pass
            def aggregate(self, *, strategy, since, min_severity):
                return agg_path

        monkeypatch.setattr("ai_hats.retro.aggregator.Aggregator", FakeAgg)

        # Stub the loader so we don't need a fully valid aggregation file.
        monkeypatch.setattr(
            "ai_hats.retro.loader.load",
            lambda _p: (SimpleNamespace(), "Body"),
        )

        handoff_calls: list[tuple[Path, str]] = []

        def fake_exec(path, kind="session"):
            handoff_calls.append((path, kind))
            # Never returns in real impl; in tests just return.

        monkeypatch.setattr("ai_hats.cli.judge.exec_claude_with_retro", fake_exec)

        result = CliRunner().invoke(main, ["judge-aggregate", "--interactive"])

        assert result.exit_code == 0, result.output
        assert handoff_calls == [(agg_path, "aggregate")]

    def test_no_interactive_skips_handoff(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".agent").mkdir()
        agg_path = tmp_path / "agg.md"
        agg_path.write_text("---\nschema: hats-aggregation/v1\n---\n# Body\n")

        class FakeAgg:
            def __init__(self, _pdir): pass
            def aggregate(self, *, strategy, since, min_severity):
                return agg_path

        monkeypatch.setattr("ai_hats.retro.aggregator.Aggregator", FakeAgg)
        monkeypatch.setattr(
            "ai_hats.retro.loader.load",
            lambda _p: (SimpleNamespace(), "Body"),
        )

        called = []
        monkeypatch.setattr(
            "ai_hats.cli.judge.exec_claude_with_retro",
            lambda *a, **kw: called.append(a),
        )

        result = CliRunner().invoke(main, ["judge-aggregate"])

        assert result.exit_code == 0, result.output
        assert called == []
