"""Unit tests for SessionReviewRunner (HATS-252).

Covers the pure-Python machinery: forbidden-key rejection, analysis-shape
validation, the merge step, and a happy-path round trip with a stubbed
SubAgentRunner.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from ai_hats.retro.facts import SessionFacts
from ai_hats.retro.session_review_runner import (
    SessionReviewError,
    SessionReviewRunner,
)
from ai_hats.retro.session_review_schema import SessionReviewV1
from ai_hats.retro.common import SessionArtifacts, SessionLinks, SessionMetrics
from ai_hats.retro.loader import load
from ai_hats.paths import hypotheses_dir
from ai_hats.paths import METRICS_JSON, REASONING_LOG, TRANSCRIPT_TXT


SID = "20260506-100000-1"


# ---- helpers ----


def _facts(sid: str = SID) -> SessionFacts:
    metrics = SessionMetrics(exit_code=0, turns=4, tool_calls=10)
    artifacts = SessionArtifacts(files_changed=["a.py"], commits=[], tasks_closed=[])
    links = SessionLinks(audit="../../../.gitlog/session_X/audit.md")
    return SessionFacts(
        session_id=sid,
        project="test",
        role="assistant",
        date=datetime(2026, 5, 6).date(),
        metrics=metrics,
        artifacts=artifacts,
        links=links,
        session_start=datetime(2026, 5, 6, 10, tzinfo=timezone.utc),
        session_end=datetime(2026, 5, 6, 11, tzinfo=timezone.utc),
    )


def _add_active_hyp(project_dir: Path, hyp_id: str = "HYP-001") -> None:
    hyps_dir = hypotheses_dir(project_dir)
    hyps_dir.mkdir(parents=True, exist_ok=True)
    (hyps_dir / f"{hyp_id}.yaml").write_text(
        "id: " + hyp_id + "\n"
        "title: t\nstatus: active\ncreated: '2026-05-01'\n"
        "source_task: TASK-001\nhypothesis: a\nvalidation_log: []\n"
    )


# ---- _check_allowed_keys ----


def test_check_allowed_keys_accepts_canonical(tmp_path: Path) -> None:
    runner = SessionReviewRunner(tmp_path)
    runner._check_allowed_keys({
        "summary": "x",
        "observations": [],
        "hypothesis_verdicts": [],
        "proposal_actions": [],
        "self_problems": [],
    })


def test_check_allowed_keys_rejects_facts(tmp_path: Path) -> None:
    """LLM is forbidden from emitting runner-injected fields."""
    runner = SessionReviewRunner(tmp_path)
    with pytest.raises(ValueError, match="forbidden"):
        runner._check_allowed_keys({"summary": "x", "metrics": {}})


# ---- _extract_yaml / _strip_code_fence (HATS-419) ----
#
# Session-reviewer often wraps the YAML body in a markdown code-fence inside
# the BEGIN/END delimiters. The parser must strip the fence; otherwise
# yaml.safe_load chokes on the leading "```yaml" character. Plain YAML must
# pass through unchanged.


def _wrap_in_delims(body: str) -> str:
    from ai_hats.retro.session_review_runner import (
        REVIEW_DELIM_START,
        REVIEW_DELIM_END,
    )
    return f"junk before\n{REVIEW_DELIM_START}\n{body}\n{REVIEW_DELIM_END}\njunk after"


def test_extract_yaml_passes_bare_yaml_unchanged(tmp_path: Path) -> None:
    runner = SessionReviewRunner(tmp_path)
    body = "summary: hi\nobservations: []\n"
    transcript = _wrap_in_delims(body)
    out = runner._extract_yaml(transcript)
    # Round-trip through yaml to confirm parseability (the actual contract).
    assert yaml.safe_load(out) == {"summary": "hi", "observations": []}


def test_extract_yaml_strips_fenced_with_yaml_lang_tag(tmp_path: Path) -> None:
    runner = SessionReviewRunner(tmp_path)
    body = "```yaml\nsummary: hi\nobservations: []\n```"
    transcript = _wrap_in_delims(body)
    out = runner._extract_yaml(transcript)
    assert not out.startswith("```")
    assert yaml.safe_load(out) == {"summary": "hi", "observations": []}


def test_extract_yaml_strips_fence_without_lang_tag(tmp_path: Path) -> None:
    runner = SessionReviewRunner(tmp_path)
    body = "```\nsummary: hi\nobservations: []\n```"
    transcript = _wrap_in_delims(body)
    out = runner._extract_yaml(transcript)
    assert not out.startswith("```")
    assert yaml.safe_load(out) == {"summary": "hi", "observations": []}


def test_strip_code_fence_handles_trailing_blank_lines(tmp_path: Path) -> None:
    """Defensive: model may emit trailing whitespace after closing fence."""
    runner = SessionReviewRunner(tmp_path)
    body = "```yaml\nsummary: hi\n```\n\n"
    out = runner._strip_code_fence(body)
    assert yaml.safe_load(out) == {"summary": "hi"}


def test_strip_code_fence_no_fence_returns_input(tmp_path: Path) -> None:
    runner = SessionReviewRunner(tmp_path)
    body = "summary: hi\nobservations: []\n"
    assert runner._strip_code_fence(body) == body


# ---- _truncate_audit (HATS-684: content-aware delivery) ----
#
# Delivery contract (supersedes the old 8KB head+tail squeeze):
#   1. Bound the redundant first-turn 👤 ingested-evidence block (PROJECT_STATE /
#      reflect-handoff / harness-context echo) — 64% of corpus bloat, redundant
#      because the reviewer already has the target's real content.
#   2. Keep ALL signal (🔧 tools / 👾 responses / real 👤 turns / tail) verbatim —
#      no tight budget; capping signal was the original cause of "cannot cite
#      evidence" → n/a verdicts.
#   3. A high ~250KB safety-valve (head/tail trim, HATS-424 tail preserved) is the
#      only hard ceiling; on the live corpus it never fires.


def _audit(first_user: str, *, role: str = "session-reviewer") -> str:
    """Minimal audit mirroring observe.py rendering: header, turn-1 👤, signal, tail."""
    return (
        "# Session Audit: 20260506-100000-1\n"
        f"- **Role**: {role}\n\n"
        "## Turn 1 (10:00:00)\n"
        f"👤 {first_user}\n\n"
        "💭 Thinking 1s\n"
        "🔧 Bash: ai-hats task show HATS-1\n"
        "👾 done\n\n"
        "## Metrics\n- **exit_code**: 0\n"
    )


def test_truncate_audit_bounds_ingested_first_user_block() -> None:
    # Reviewer/judge first turn = giant ingested PROJECT_STATE echo (redundant).
    ingested = "# PROJECT_STATE\n" + ("- **HATS-999**: backlog dump line\n" * 2000)
    text = _audit(ingested)
    assert len(text) > 50_000
    out = SessionReviewRunner._truncate_audit(text)
    # Ingested block bounded near the cap (not the full 60KB+).
    assert len(out) < SessionReviewRunner._INGESTED_CAP + 4000
    assert "# PROJECT_STATE" in out, "head of ingested block (incl. any real ask) kept"
    assert "elided" in out, "elision marker announces the bounded drop"
    # Signal AFTER the ingested block survives verbatim.
    assert "🔧 Bash: ai-hats task show HATS-1" in out
    assert "👾 done" in out
    assert "## Metrics" in out


def test_truncate_audit_keeps_signal_verbatim() -> None:
    # Signal-dense audit (300 real tool calls) under the valve must NOT be cut —
    # no tight budget anymore; signal flows to the reviewer.
    tools = "".join(f"🔧 Bash: command number {i}\n" for i in range(300))
    text = (
        "# Session Audit\n- **Role**: maintainer\n\n"
        "## Turn 1 (10:00:00)\n👤 давай возьмем 684\n\n"
        + tools
        + "👾 SENTINEL_RESPONSE\n\n## Metrics\n- **exit_code**: 0\n"
    )
    assert 8000 < len(text) < SessionReviewRunner._SAFETY_VALVE
    out = SessionReviewRunner._truncate_audit(text)
    assert out == text, "signal-dense audit under the safety valve passes through uncut"


def test_truncate_audit_short_passes_through_unchanged() -> None:
    text = _audit("давай возьмем 684")  # small everywhere
    assert SessionReviewRunner._truncate_audit(text) == text


def test_truncate_audit_preserves_real_request_head() -> None:
    # Interactive target: real ask at the head, then giant harness-context echo.
    real_ask = "давай посмотрим задачку HATS-524 и сделаем ревью"
    harness = "\n" + ("CLAUDE.md injected line\n" * 2000)
    text = _audit(real_ask + harness)
    out = SessionReviewRunner._truncate_audit(text)
    assert real_ask in out, "the real request at the head must survive bounding"
    assert "🔧 Bash: ai-hats task show HATS-1" in out, "post-turn signal survives"
    assert len(out) < SessionReviewRunner._INGESTED_CAP + 4000, "harness echo bounded"


def test_truncate_audit_safety_valve_preserves_head_and_tail() -> None:
    head_sentinel = "HEAD_SENTINEL_VISIBLE_TO_REVIEWER"
    tail_sentinel = "TAIL_SENTINEL_END_OF_SESSION"  # HATS-424 tail
    # No 👤 block to bound; pure size > valve → head/tail trim fires.
    body = "z" * (SessionReviewRunner._SAFETY_VALVE + 50_000)
    text = head_sentinel + body + tail_sentinel
    out = SessionReviewRunner._truncate_audit(text)
    assert head_sentinel in out, "head sentinel must survive the valve"
    assert tail_sentinel in out, "HATS-424: end-of-session tail must survive"
    assert "truncated from middle" in out, "marker must announce the gap"
    assert len(out) < len(text), "valve must shrink"


# ---- _validate_analysis_shape ----


def test_validate_analysis_shape_requires_summary(tmp_path: Path) -> None:
    runner = SessionReviewRunner(tmp_path)
    with pytest.raises(ValueError, match="summary"):
        runner._validate_analysis_shape({"summary": ""}, SID)


def test_validate_analysis_shape_requires_active_hyp_coverage(tmp_path: Path) -> None:
    _add_active_hyp(tmp_path, "HYP-042")
    runner = SessionReviewRunner(tmp_path)
    with pytest.raises(ValueError, match="HYP-042"):
        runner._validate_analysis_shape(
            {"summary": "ok", "hypothesis_verdicts": []}, SID,
        )


def test_validate_analysis_shape_passes_with_full_coverage(tmp_path: Path) -> None:
    _add_active_hyp(tmp_path, "HYP-042")
    runner = SessionReviewRunner(tmp_path)
    runner._validate_analysis_shape(
        {
            "summary": "ok",
            "hypothesis_verdicts": [
                {
                    "hyp_id": "HYP-042",
                    "verdict": "inconclusive",
                    "evidence": "no signal",
                    "recommendation": "keep",
                },
            ],
        },
        SID,
    )


# ---- _coerce_observations (HATS-610) ----
#
# The session-reviewer LLM occasionally emits an observation bullet as a
# single-key mapping ({title: detail}) instead of a string. observations is
# list[str] with extra=forbid, so a dict entry crashes _merge OUTSIDE the
# retry loop — un-recoverable. Coerce (non-critical narrative) rather than
# crash; HYP/PROP refs stay strict.


def test_coerce_observations_passes_strings_through() -> None:
    runner = SessionReviewRunner  # static method — no instance needed
    assert runner._coerce_observations(["a", "b"]) == ["a", "b"]


def test_coerce_observations_flattens_single_key_dict() -> None:
    out = SessionReviewRunner._coerce_observations(
        [{"Session exited normally": "composition initialization"}]
    )
    assert out == ["Session exited normally: composition initialization"]


def test_coerce_observations_flattens_multi_key_dict() -> None:
    out = SessionReviewRunner._coerce_observations([{"a": 1, "b": 2}])
    assert out == ["a: 1, b: 2"]


def test_coerce_observations_stringifies_other_scalars() -> None:
    out = SessionReviewRunner._coerce_observations([42, None, ["x"]])
    assert out == ["42", "None", "['x']"]


def test_coerce_observations_mixed_list() -> None:
    out = SessionReviewRunner._coerce_observations(
        ["plain bullet", {"dict obs": "detail"}]
    )
    assert out == ["plain bullet", "dict obs: detail"]


def test_coerce_observations_empty_and_none_return_empty_list() -> None:
    assert SessionReviewRunner._coerce_observations([]) == []
    assert SessionReviewRunner._coerce_observations(None) == []


# ---- _merge ----


def test_merge_produces_valid_review(tmp_path: Path) -> None:
    runner = SessionReviewRunner(tmp_path)
    review = runner._merge(_facts(), {
        "summary": "did stuff",
        "observations": ["obs1"],
        "hypothesis_verdicts": [],
        "proposal_actions": [],
        "self_problems": [],
    })
    assert isinstance(review, SessionReviewV1)
    assert review.metrics.turns == 4
    assert review.artifacts.files_changed == ["a.py"]
    assert review.summary == "did stuff"


def test_save_round_trips_through_loader(tmp_path: Path) -> None:
    runner = SessionReviewRunner(tmp_path)
    review = runner._merge(_facts(), {"summary": "s"})
    path = runner._save(review)
    loaded, _body = load(path)
    assert isinstance(loaded, SessionReviewV1)
    assert loaded.session_id == SID


# ---- happy-path with stubbed SubAgentRunner ----


class _FakeSubAgentSession:
    def __init__(
        self,
        transcript_text: str,
        session_dir: Path,
        *,
        session_id: str = "20260101-000000-2",
        write_transcript: bool = True,
        metrics: dict | None = None,
        reasoning: str | None = None,
    ) -> None:
        self.session_dir = session_dir
        self.session_id = session_id
        self.metrics_path = session_dir / METRICS_JSON
        if write_transcript:
            (session_dir / TRANSCRIPT_TXT).write_text(transcript_text)
        if metrics is not None:
            self.metrics_path.write_text(json.dumps(metrics))
        if reasoning is not None:
            (session_dir / REASONING_LOG).write_text(reasoning)


class _FakeSubAgentRunner:
    """Minimal stub matching SubAgentRunner.run signature."""

    def __init__(
        self,
        transcript_text: str,
        scratch: Path,
        *,
        write_transcript: bool = True,
        metrics: dict | None = None,
        reasoning: str | None = None,
    ) -> None:
        self._transcript = transcript_text
        self._scratch = scratch
        self._write_transcript = write_transcript
        self._metrics = metrics
        self._reasoning = reasoning

    def run(self, **_kw):  # noqa: ANN003 — test stub matches keyword API
        sdir = self._scratch / "sub-session"
        sdir.mkdir(exist_ok=True)
        return _FakeSubAgentSession(
            self._transcript,
            sdir,
            write_transcript=self._write_transcript,
            metrics=self._metrics,
            reasoning=self._reasoning,
        )


def _stub_facts(monkeypatch, project_dir: Path) -> None:
    from ai_hats.retro import session_review_runner as mod

    monkeypatch.setattr(mod, "compute_facts", lambda pd, sid: _facts(sid))


def test_run_writes_artifact_and_round_trips(tmp_path: Path, monkeypatch) -> None:
    _stub_facts(monkeypatch, tmp_path)

    transcript = (
        "noise BEGIN_REFLECT_SESSION_RETRO\n"
        + yaml.safe_dump({
            "summary": "what happened",
            "observations": ["o1"],
            "hypothesis_verdicts": [],
            "proposal_actions": [],
            "self_problems": [],
        })
        + "\nEND_REFLECT_SESSION_RETRO trailing\n"
    )
    fake_runner = _FakeSubAgentRunner(transcript, tmp_path)

    runner = SessionReviewRunner(tmp_path, subagent_runner=fake_runner)
    path = runner.run(SID)

    loaded, _body = load(path)
    assert isinstance(loaded, SessionReviewV1)
    assert loaded.summary == "what happened"
    assert loaded.metrics.turns == 4  # facts merged in


def test_run_coerces_dict_observation_instead_of_crashing(
    tmp_path: Path, monkeypatch,
) -> None:
    """HATS-610 regression: a dict-shaped observation entry must NOT crash
    the retro. Before the fix this passed _validate_analysis_shape (IS-A-LIST
    only) then died terminally in _merge (list[str], extra=forbid), with the
    failure landing OUTSIDE the retry loop. The runner must coerce it to a
    string and persist a valid review."""
    _stub_facts(monkeypatch, tmp_path)
    transcript = (
        "BEGIN_REFLECT_SESSION_RETRO\n"
        + yaml.safe_dump({
            "summary": "what happened",
            "observations": [
                "a plain bullet",
                {"Session exited normally": "composition initialization"},
            ],
            "hypothesis_verdicts": [],
            "proposal_actions": [],
            "self_problems": [],
        })
        + "\nEND_REFLECT_SESSION_RETRO\n"
    )
    fake_runner = _FakeSubAgentRunner(transcript, tmp_path)
    runner = SessionReviewRunner(tmp_path, subagent_runner=fake_runner)
    # max_retries=0 — the coercion fix must succeed on the FIRST attempt,
    # proving the failure mode is gone (it was never retry-recoverable).
    path = runner.run(SID, max_retries=0)

    loaded, _body = load(path)
    assert isinstance(loaded, SessionReviewV1)
    assert loaded.observations == [
        "a plain bullet",
        "Session exited normally: composition initialization",
    ]


def test_run_raises_session_review_error_on_invalid_llm_output(
    tmp_path: Path, monkeypatch,
) -> None:
    _stub_facts(monkeypatch, tmp_path)
    transcript = (
        "BEGIN_REFLECT_SESSION_RETRO\n"
        + yaml.safe_dump({"summary": ""})  # empty summary → fails validation
        + "\nEND_REFLECT_SESSION_RETRO\n"
    )
    fake_runner = _FakeSubAgentRunner(transcript, tmp_path)
    runner = SessionReviewRunner(tmp_path, subagent_runner=fake_runner)
    with pytest.raises(SessionReviewError):
        runner.run(SID, max_retries=0)


# ---- HATS-271: empty sub-agent transcript surfaces real diagnostics ----


def test_run_surfaces_subagent_failure_when_transcript_missing(
    tmp_path: Path, monkeypatch,
) -> None:
    """Sub-agent crashed before writing transcript.txt → must NOT loop on
    'Empty frontmatter'; must surface exit_code/error from metrics.json so
    the harness records a meaningful cause in retro.log."""
    _stub_facts(monkeypatch, tmp_path)
    fake_runner = _FakeSubAgentRunner(
        "", tmp_path,
        write_transcript=False,
        metrics={"exit_code": 124, "timed_out": True},
        reasoning="claude: request timed out\n",
    )
    runner = SessionReviewRunner(tmp_path, subagent_runner=fake_runner)
    with pytest.raises(SessionReviewError) as excinfo:
        runner.run(SID, max_retries=2)

    msg = str(excinfo.value)
    assert "sub-agent produced no output" in msg
    assert "exit_code=124" in msg
    assert "timed_out=True" in msg
    assert "request timed out" in msg
    # Critically, the misleading "Empty frontmatter" wording from the
    # validator path must NOT be the surface error here.
    assert "Empty frontmatter" not in msg


def test_run_surfaces_subagent_failure_when_transcript_blank(
    tmp_path: Path, monkeypatch,
) -> None:
    """transcript.txt exists but contains only whitespace — same failure
    mode as missing file: do not retry, surface diagnostics."""
    _stub_facts(monkeypatch, tmp_path)
    fake_runner = _FakeSubAgentRunner(
        "   \n\n",  # whitespace-only
        tmp_path,
        write_transcript=True,
        metrics={"exit_code": 1},
    )
    runner = SessionReviewRunner(tmp_path, subagent_runner=fake_runner)
    with pytest.raises(SessionReviewError) as excinfo:
        runner.run(SID, max_retries=3)

    msg = str(excinfo.value)
    assert "sub-agent produced no output" in msg
    assert "exit_code=1" in msg
    assert "Empty frontmatter" not in msg


# --- HATS-442: composition rendering ----------------------------------------


def test_render_composition_returns_empty_string_for_no_snapshot():
    """Pre-HATS-442 sessions (composition is None) → no section emitted."""
    from ai_hats.retro.session_review_runner import SessionReviewRunner

    facts = _facts()  # default has no composition
    assert SessionReviewRunner._render_composition(facts) == ""


def test_render_composition_emits_role_and_layer_tags():
    from ai_hats.retro.session_review_runner import SessionReviewRunner

    facts = _facts()
    facts.composition = {
        "traits": ["trait-base", "personal-workflow"],
        "rules": ["global_rule_destructive_actions"],
        "skills": ["design-minimalism"],
        "provenance": {
            "traits": {
                "trait-base": "built-in",
                "personal-workflow": "global",
            },
            "rules": {"global_rule_destructive_actions": "built-in"},
            "skills": {"design-minimalism": "built-in"},
        },
    }
    out = SessionReviewRunner._render_composition(facts)
    assert "## Effective composition" in out
    assert f"Role: {facts.role}" in out
    assert "trait-base (built-in)" in out
    assert "personal-workflow (global)" in out
    assert "global_rule_destructive_actions (built-in)" in out
    assert "design-minimalism (built-in)" in out
    # Layer-tags glossary present (helps the LLM use them in proposals).
    assert "(built-in)" in out
    assert "(global)" in out
    assert "(project)" in out


def test_render_composition_handles_empty_buckets():
    """A role with no skills should still render — just print '(none)'
    rather than crash or emit a malformed line."""
    from ai_hats.retro.session_review_runner import SessionReviewRunner

    facts = _facts()
    facts.composition = {
        "traits": ["trait-base"],
        "rules": [],
        "skills": [],
        "provenance": {"traits": {"trait-base": "built-in"}, "rules": {}, "skills": {}},
    }
    out = SessionReviewRunner._render_composition(facts)
    assert "Skills: (none)" in out
    assert "Rules:  (none)" in out
    assert "trait-base (built-in)" in out


def test_render_composition_provenance_defaults_to_built_in():
    """When provenance map is missing an entry, the renderer falls back
    to 'built-in' — safe-default for partial maps."""
    from ai_hats.retro.session_review_runner import SessionReviewRunner

    facts = _facts()
    facts.composition = {
        "traits": ["unmapped"],
        "rules": [],
        "skills": [],
        "provenance": {"traits": {}, "rules": {}, "skills": {}},
    }
    out = SessionReviewRunner._render_composition(facts)
    assert "unmapped (built-in)" in out


# ---- HATS-534: verification_protocol surfacing ----


def _write_hyp_with_extras(project_dir: Path, hyp_id: str, **extras) -> None:
    """Write a HYP yaml carrying arbitrary extra fields (via extra='allow').

    Used by HATS-534 tests to verify the renderer surfaces fields stored
    outside the typed schema (verification_protocol in particular).
    """
    hyps_dir = hypotheses_dir(project_dir)
    hyps_dir.mkdir(parents=True, exist_ok=True)
    body = {
        "id": hyp_id,
        "title": f"t-{hyp_id}",
        "status": "active",
        "created": "2026-05-26",
        "source_task": "HATS-001",
        "hypothesis": "test hypothesis",
        "success_criterion": "criterion text",
        "observation_window": "4 sessions",
        "validation_log": [],
    }
    body.update(extras)
    (hyps_dir / f"{hyp_id}.yaml").write_text(yaml.safe_dump(body))


def test_render_active_hypotheses_surfaces_verification_protocol(tmp_path: Path):
    """HATS-534 — verification_protocol on a HYP must render into the
    session-reviewer handoff so the auditor can follow Step 1.5."""
    from ai_hats.retro.session_review_runner import SessionReviewRunner

    protocol = (
        "STRICT — auditor evidence MUST be exactly three lines:\n"
        "Line 1: CRITERION: <verbatim>\n"
        "Line 2: OBSERVED: <verbatim or NOT OBSERVED>\n"
        "Line 3: VERDICT_REASON: satisfies | fails | silent"
    )
    _write_hyp_with_extras(
        tmp_path, "HYP-501", verification_protocol=protocol
    )

    runner = SessionReviewRunner(tmp_path)
    out = runner._render_active_hypotheses()

    assert "verification_protocol: |" in out, (
        "expected literal-block-scalar header for verification_protocol"
    )
    # Verbatim body indented under the block scalar.
    assert "    STRICT — auditor evidence MUST be exactly three lines:" in out
    assert "    Line 1: CRITERION: <verbatim>" in out
    assert "    Line 3: VERDICT_REASON: satisfies | fails | silent" in out


def test_render_active_hypotheses_omits_verification_protocol_when_absent(
    tmp_path: Path,
):
    """HATS-534 — HYPs without verification_protocol must not gain a stray
    label (no `verification_protocol: None` leftovers). Legacy HYPs render
    identically to before."""
    from ai_hats.retro.session_review_runner import SessionReviewRunner

    _write_hyp_with_extras(tmp_path, "HYP-502")  # no verification_protocol

    runner = SessionReviewRunner(tmp_path)
    out = runner._render_active_hypotheses()

    assert "HYP-502" in out
    assert "verification_protocol" not in out
