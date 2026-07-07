"""SessionReviewRunner — single-LLM-call post-session review (HATS-252).

Replaces the prior two-step flow (SessionRetroBuilder → ReflectSessionRunner).
Pure-Python computes factual fields via :mod:`facts`; one LLM call (role
``session-reviewer``) returns the analysis fields; runner merges and writes
``.agent/retrospectives/sessions/<session_id>.md`` (schema
``hats-session-review/v1``).

Failure-proposal filing lives in the harness layer
(:mod:`ai_hats.cli.reflect_session_main`) — single ownership, no double-fire.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import ValidationError

from ..harness.diagnostic import diagnose_silent_session
from ..harness.errors import HarnessReliabilityError
from ai_hats_tracker.hypothesis import HypothesisStore, ProposalStore
from ..paths import AUDIT_MD, METRICS_JSON, PROJECT_CONFIG, TRANSCRIPT_TXT, session_dirname
from .facts import compute_facts
from .loader import parse
from .reflect_session_schema import HypothesisVerdict, ProposalAction
from .session_review_schema import SCHEMA_VERSION, SessionReviewV1
from .writer import dump

if TYPE_CHECKING:
    from ..pipeline.harness_policy import HarnessPolicy
    from ..runtime import SubAgentRunner

logger = logging.getLogger(__name__)

REVIEW_DELIM_START = "BEGIN_REFLECT_SESSION_RETRO"
REVIEW_DELIM_END = "END_REFLECT_SESSION_RETRO"

#: keys the LLM is allowed to emit in its frontmatter — the runner injects the rest
_ALLOWED_LLM_KEYS = {
    "summary",
    "observations",
    "hypothesis_verdicts",
    "proposal_actions",
    "self_problems",
}


class SessionReviewError(Exception):
    """Session-review run failed; harness layer is responsible for the meta-proposal."""


class SessionReviewRunner:
    """Run session-reviewer on one session and persist a SessionReviewV1."""

    def __init__(
        self,
        project_dir: Path,
        *,
        subagent_runner: "SubAgentRunner | None" = None,
    ) -> None:
        from ..paths import hypotheses_dir, proposals_dir, retros_dir, runs_dir

        self.project_dir = project_dir
        self.out_dir = retros_dir(project_dir) / "sessions"
        self.gitlog_dir = runs_dir(project_dir)
        self.hypotheses = HypothesisStore(hypotheses_dir(project_dir))
        self.proposals = ProposalStore(proposals_dir(project_dir))
        self._subagent_runner = subagent_runner

    # ---- public API ----

    def run(
        self,
        session_id: str,
        *,
        max_retries: int = 1,
        harness_policy: "HarnessPolicy | None" = None,
    ) -> Path:
        """Run session-reviewer on ``session_id``. Returns path to saved review.

        Raises :class:`SessionReviewError` on validation/schema failure
        (target=session-reviewer). Raises :class:`HarnessReliabilityError`
        (HATS-378) when the harness-layer guard fires — those propagate
        unwrapped so callers can route the meta-PROP to
        ``target=harness-incident``.
        """
        try:
            facts = compute_facts(self.project_dir, session_id)
            prompt = self._build_prompt(facts)
            analysis = self._run_and_validate(
                prompt, facts.session_id, max_retries,
                harness_policy=harness_policy,
            )
            review = self._merge(facts, analysis)
            self._validate_integrity(review, facts.session_id)
            return self._save(review)
        except HarnessReliabilityError:
            # Don't wrap — caller routes harness-incident vs
            # session-reviewer via meta-PROP target (HATS-378 Phase 3).
            raise
        except Exception as exc:  # noqa: BLE001 — surface every failure to harness
            error_msg = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "session-reviewer failed for %s: %s", session_id, error_msg,
                exc_info=True,
            )
            raise SessionReviewError(error_msg) from exc

    # ---- prompt ----

    def _build_prompt(self, facts) -> str:
        sid = facts.session_id
        sections: list[str] = []
        sections.append(
            "You are running as the session-reviewer role over ONE session.\n"
            f"Session id: {sid}\n"
            f"Emit a YAML mapping between {REVIEW_DELIM_START} and "
            f"{REVIEW_DELIM_END}. The mapping MUST contain ONLY: summary, "
            "observations, hypothesis_verdicts, proposal_actions, self_problems."
        )
        sections.append(self._render_active_hypotheses())
        sections.append(self._render_open_proposals())
        composition_section = self._render_composition(facts)
        if composition_section:
            sections.append(composition_section)
        sections.append(self._render_session_evidence(sid))
        sections.append(
            "## Output requirements (STRICT — extras are rejected)\n\n"
            "The runner injects schema/session_id/timestamp/project/role/date/"
            "metrics/artifacts/links. You emit ONLY:\n\n"
            "  summary: <one-paragraph narrative>\n"
            "  observations:\n"
            "    - <bullet>\n"
            "    - ...                          # 0..6 bullets typical\n"
            "  hypothesis_verdicts:\n"
            "    - hyp_id: HYP-NNN\n"
            "      verdict: confirmed | refuted | inconclusive | n/a\n"
            "      evidence: <one-line cite>\n"
            "      recommendation: close_confirmed | close_refuted | "
            "keep | extend_window\n"
            "    (one entry PER active HYP listed above; do not skip)\n"
            "  proposal_actions:\n"
            "    - action: created | voted\n"
            "      prop_id: PROP-NNN\n"
            "  self_problems: [PROP-NNN, ...]\n\n"
            "Before listing a proposal action, USE THE CLI to materialize it:\n"
            f"  ai-hats task proposal create --title ... --category ... "
            f"--target ... --description ... --rationale ... --session {sid}\n"
            f"  ai-hats task proposal vote --prop PROP-NNN --session {sid} "
            "--reasoning ...\n"
            "If you cannot follow the format or hit a meta-problem, file:\n"
            "  ai-hats task proposal create --category process --target "
            "session-reviewer \\\n"
            f"    --title <short> --description <what failed> --rationale <why> "
            f"--session {sid}\n"
            "and reference the resulting PROP-NNN in `self_problems`. "
            "NEVER silently drop entries."
        )
        sections.append(
            f"\n{REVIEW_DELIM_START}\n... your YAML here "
            f"...\n{REVIEW_DELIM_END}\n"
        )
        return "\n\n".join(sections)

    def _render_active_hypotheses(self) -> str:
        active = self.hypotheses.list_active()
        if not active:
            return "## Active hypotheses\n\n(none — emit empty hypothesis_verdicts list)"
        lines = ["## Active hypotheses (vote per each below — do not skip)"]
        for h in active:
            lines.append(
                f"- **{h.id}** — {h.title}\n"
                f"  success_criterion: {h.success_criterion!r}\n"
                f"  observation_window: {h.observation_window!r}"
            )
            # HATS-534 — surface verification_protocol when the HYP carries one
            # (stored via Hypothesis.extra="allow"). Renders as a YAML literal
            # block scalar so multi-line protocols stay verbatim and the auditor
            # can quote from them per review-hypothesis Step 1.5.
            vp = getattr(h, "verification_protocol", None)
            if vp:
                indented = "\n".join(f"    {line}" for line in str(vp).splitlines())
                lines.append(f"  verification_protocol: |\n{indented}")
        return "\n".join(lines)

    @staticmethod
    def _render_composition(facts) -> str:
        """HATS-442: surface the effective composition with source-tags.

        Returns an empty string when the snapshot is absent (old sessions),
        so the prompt stays clean for legacy data.
        """
        composition = getattr(facts, "composition", None) or {}
        if not composition:
            return ""
        prov = composition.get("provenance", {}) or {}

        def _fmt(names: list[str], layer_map: dict) -> str:
            if not names:
                return "(none)"
            return ", ".join(
                f"{n} ({layer_map.get(n, 'built-in')})" for n in names
            )

        lines = [
            "## Effective composition (what actually loaded for this session)",
            "",
            f"Role: {facts.role}",
            f"Traits: {_fmt(composition.get('traits', []) or [], prov.get('traits', {}))}",
            f"Rules:  {_fmt(composition.get('rules', []) or [], prov.get('rules', {}))}",
            f"Skills: {_fmt(composition.get('skills', []) or [], prov.get('skills', {}))}",
            "",
            "Layer tags: `(built-in)` = ships with ai-hats; `(global)` = the "
            "user's `~/.ai-hats/customizations.yaml`; `(project)` = this "
            "project's `ai-hats.yaml::customizations`. When citing why a "
            "behaviour occurred (or didn't), the source-tag tells you whether "
            "the issue belongs to framework defaults, the user's personal "
            "overlay, or this project's overlay — useful for the proposal's "
            "`target` field.",
        ]
        return "\n".join(lines)

    def _render_open_proposals(self) -> str:
        open_props = self.proposals.filter(status="open")
        if not open_props:
            return (
                "## Open proposals\n\n(inbox empty — create new ones with "
                "`ai-hats task proposal create` if you spot improvements)"
            )
        lines = ["## Open proposals (vote on similar; create only if novel)"]
        for p in open_props:
            lines.append(
                f"- **{p.id}** [{p.category}/{p.target}] {p.title}\n"
                f"  description: {p.description}"
            )
        return "\n".join(lines)

    def _render_session_evidence(self, session_id: str) -> str:
        sdir = self.gitlog_dir / session_dirname(session_id)
        parts = [f"## Session evidence for {session_id}"]
        metrics_path = sdir / METRICS_JSON
        if metrics_path.exists():
            try:
                metrics = json.loads(metrics_path.read_text())
                parts.append(
                    f"metrics.json:\n```json\n"
                    f"{json.dumps(metrics, indent=2)}\n```"
                )
            except json.JSONDecodeError:
                pass
        audit_path = sdir / AUDIT_MD
        if audit_path.exists():
            audit_text = self._truncate_audit(audit_path.read_text())
            parts.append(f"audit.md:\n```\n{audit_text}\n```")
        return "\n\n".join(parts)

    # HATS-684: content-aware audit *delivery* (generation stays lossless,
    # HATS-681/666/683). The bulk of audit bytes is the first-turn 👤
    # ingested-evidence echo (PROJECT_STATE backlog dump / Reflect-all handoff /
    # harness-context) — redundant, since the reviewer already has the target's
    # real content. So: (1) bound that first-turn block to a small head-keep cap
    # (a real request sits at its head); (2) keep ALL real signal verbatim — NO
    # tight budget. Capping signal was itself the cause of "cannot cite
    # evidence" → n/a verdicts (the HATS-666/680 chain). A high safety-valve
    # catches pathological runaways only.
    _INGESTED_CAP = 2000  # bound the first-turn 👤 ingested-evidence echo

    # HATS-424 invariant: end-of-session events (self-retro Skill calls, final
    # commits, transitions, judge-report writes) live in the audit tail. The
    # safety-valve trim keeps both ends so late-session signal survives even a
    # pathological-size audit.
    _SAFETY_VALVE = 250_000  # absolute ceiling; never fires on the live corpus
    _VALVE_HEAD = 8000
    _VALVE_TAIL = 8000

    @classmethod
    def _truncate_audit(cls, text: str) -> str:
        text = cls._bound_first_user_block(text)
        if len(text) <= cls._SAFETY_VALVE:
            return text
        dropped = len(text) - (cls._VALVE_HEAD + cls._VALVE_TAIL)
        marker = f"\n... ({dropped} bytes truncated from middle) ...\n"
        return text[: cls._VALVE_HEAD] + marker + text[-cls._VALVE_TAIL :]

    @classmethod
    def _bound_first_user_block(cls, text: str) -> str:
        """Head-keep the first-turn 👤 block to ``_INGESTED_CAP`` bytes.

        The block runs from the first ``👤`` line to the next signal marker
        (``🔧``/``👾``/``💭``) or ``## Turn`` header. Conservative by design: when
        ingested evidence embeds a nested audit (reviewer-as-target), the block is
        delimited at the first nested marker — which only bounds *more* of the
        redundant echo, never real signal. Real requests, when present, sit at the
        block head and survive the head-keep.
        """
        m = re.search(r"^👤 ", text, re.M)
        if not m:
            return text
        start = m.start()
        nxt = re.search(r"^(?:🔧|👾|💭|## Turn \d+)", text[m.end() :], re.M)
        end = m.end() + nxt.start() if nxt else len(text)
        block = text[start:end]
        if len(block) <= cls._INGESTED_CAP:
            return text
        elided = len(block) - cls._INGESTED_CAP
        bounded = (
            block[: cls._INGESTED_CAP]
            + f"\n…[ingested-evidence bounded: {elided} bytes elided]…\n"
        )
        return text[:start] + bounded + text[end:]

    # ---- run + validate ----

    def _get_runner(self) -> "SubAgentRunner":
        if self._subagent_runner is not None:
            return self._subagent_runner
        from ..composition_seam import build_composition_payload, make_session_manager
        from ..runtime import SubAgentRunner

        # HATS-865: compose ONCE at this integrator-side seam; the retry loop
        # in _run_and_validate shares the composition. strict=False — a broken
        # session-reviewer role surfaces via HATS-271 (empty transcript), not
        # a seam raise.
        payload = build_composition_payload(
            self.project_dir, role_override="session-reviewer", strict=False,
        )
        return SubAgentRunner(
            self.project_dir,
            payload,
            session_mgr=make_session_manager(self.project_dir),
        )

    def _review_model(self) -> str:
        """Read ``feedback.session_retro.review_model`` (with deprecated alias).

        Returns empty string when unset — SubAgentRunner treats falsy as
        "no override" and the provider CLI's default applies.
        """
        from ..models import ProjectConfig

        cfg_path = self.project_dir / PROJECT_CONFIG
        if not cfg_path.exists():
            return ""
        try:
            cfg = ProjectConfig.from_yaml(cfg_path)
        except Exception:
            return ""
        sr = cfg.feedback.session_retro
        return (sr.review_model or sr.reflect_model or "")

    def _run_and_validate(
        self,
        prompt: str,
        session_id: str,
        max_retries: int,
        *,
        harness_policy: "HarnessPolicy | None" = None,
    ) -> dict[str, Any]:
        runner = self._get_runner()
        review_model = self._review_model()
        attempt = 0
        current_prompt = prompt
        last_error: Exception | None = None
        last_md = ""
        while True:
            session = runner.run(
                task=current_prompt,
                model=review_model,
                # NONE: sub-agent needs access to real .agent/ (gitignored,
                # invisible inside a worktree) so its CLI calls land in the
                # project's hypothesis backlog. Trust model: role injection
                # forbids non-CLI mutations.
                isolation_mode="none",
                harness_policy=harness_policy,
            )
            transcript_path = session.session_dir / TRANSCRIPT_TXT
            transcript = (
                transcript_path.read_text() if transcript_path.exists() else ""
            )
            # HATS-271: empty transcript means the sub-agent itself failed
            # (subprocess timeout, claude CLI error, auth/quota issue) — not
            # a schema mismatch. Retrying with a "fix your YAML" prompt is
            # pointless and produces a misleading "Empty frontmatter" final
            # error that hides the real cause. Surface the failure with the
            # sub-agent's diagnostic context immediately.
            if not transcript.strip():
                raise ValueError(
                    "session-reviewer sub-agent produced no output: "
                    f"{diagnose_silent_session(session)}"
                )
            md = self._extract_yaml(transcript)
            last_md = md
            try:
                raw, _body = parse(md if md.startswith("---\n") else f"---\n{md}\n---\n")
                self._check_allowed_keys(raw)
                # Light shape validation only — full SessionReviewV1 happens
                # after merging facts.
                self._validate_analysis_shape(raw, session_id)
                # HATS-610: normalise non-string observation entries BEFORE
                # the strict list[str] validation in _merge (which runs
                # outside this retry loop). A dict-shaped observation would
                # otherwise pass the lenient shape check, return here, then
                # crash terminally in _merge with no chance to retry.
                if "observations" in raw:
                    raw["observations"] = self._coerce_observations(
                        raw["observations"]
                    )
                return raw
            except (ValidationError, ValueError, yaml.YAMLError) as e:
                last_error = e
                if attempt >= max_retries:
                    break
                attempt += 1
                current_prompt = self._retry_prompt(prompt, md, str(e))
        raise ValueError(
            f"session-reviewer output failed validation after "
            f"{attempt + 1} attempt(s): {last_error}\n"
            f"Last extracted YAML (truncated):\n{last_md[:500]}"
        )

    def _extract_yaml(self, transcript: str) -> str:
        if not transcript:
            return ""
        s = transcript.find(REVIEW_DELIM_START)
        if s >= 0:
            e = transcript.find(REVIEW_DELIM_END, s + len(REVIEW_DELIM_START))
            if e > s:
                body = transcript[s + len(REVIEW_DELIM_START):e].strip()
                return self._strip_code_fence(body)
        return self._strip_code_fence(transcript)

    @staticmethod
    def _strip_code_fence(body: str) -> str:
        """Strip a surrounding markdown code-fence (```yaml ... ``` or ``` ... ```).

        Defensive: the session-reviewer prompt asks for raw YAML between the
        REVIEW_DELIM markers, but the model often wraps it in a markdown
        code-fence. Stripping in the parser is deterministic; prompt
        instructions are not. Plain YAML (no fence) passes through unchanged.
        """
        if not body:
            return body
        stripped = body.strip()
        if not stripped.startswith("```"):
            return body
        lines = stripped.splitlines()
        # First line is fence opener (```yaml, ``` or ```<lang>); drop it.
        # Walk from the end to find the closing fence; drop trailing blanks too.
        inner = lines[1:]
        while inner and not inner[-1].strip():
            inner.pop()
        if inner and inner[-1].strip() == "```":
            inner.pop()
        return "\n".join(inner).strip()

    @staticmethod
    def _check_allowed_keys(raw: dict[str, Any]) -> None:
        extras = set(raw.keys()) - _ALLOWED_LLM_KEYS
        if extras:
            raise ValueError(
                "session-reviewer output contains forbidden keys "
                f"(facts are runner-injected): {sorted(extras)}"
            )

    def _validate_analysis_shape(
        self, raw: dict[str, Any], session_id: str
    ) -> None:
        """Validate analysis dict in isolation — fast feedback for retry loop."""
        summary = raw.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("missing or empty `summary`")
        if "observations" in raw and not isinstance(raw["observations"], list):
            raise ValueError("`observations` must be a list")
        verdicts = raw.get("hypothesis_verdicts", []) or []
        if not isinstance(verdicts, list):
            raise ValueError("`hypothesis_verdicts` must be a list")
        # Construct each verdict to surface field-level errors here, not after merge.
        for entry in verdicts:
            HypothesisVerdict.model_validate(entry)
        for entry in raw.get("proposal_actions", []) or []:
            ProposalAction.model_validate(entry)
        active_ids = {h.id for h in self.hypotheses.list_active()}
        verdict_ids = {v["hyp_id"] for v in verdicts if isinstance(v, dict)}
        missing = active_ids - verdict_ids
        if missing:
            raise ValueError(
                "hypothesis_verdicts missing entries for active HYPs: "
                f"{sorted(missing)}"
            )

    @staticmethod
    def _coerce_observations(observations: Any) -> list[str]:
        """Coerce observation entries to strings (HATS-610).

        The session-reviewer LLM occasionally emits an ``observations``
        bullet as a single-key mapping (``{'<title>': '<detail>'}``)
        instead of a plain string. ``SessionReviewV1.observations`` is
        ``list[str]`` (``extra="forbid"``), so such an entry crashes
        ``_merge`` *outside* the retry loop — un-recoverable, killing the
        whole retro (root cause of the flaky e2e in HATS-610).

        ``observations`` are non-critical narrative, so coerce rather than
        crash (HYP/PROP refs stay strict — those mutate the tracker):

        - ``str``  → kept verbatim
        - ``dict`` → ``"k: v, ..."`` (e.g. ``{title: detail}`` → ``"title: detail"``)
        - other    → ``str(entry)``

        By the time this runs ``_validate_analysis_shape`` has already
        guaranteed ``observations`` is a list (or absent); the falsy guard
        also covers ``None``/``[]``.
        """
        if not observations:
            return []
        coerced: list[str] = []
        for entry in observations:
            if isinstance(entry, str):
                coerced.append(entry)
            elif isinstance(entry, dict):
                coerced.append(
                    ", ".join(f"{k}: {v}" for k, v in entry.items())
                )
            else:
                coerced.append(str(entry))
        return coerced

    def _retry_prompt(self, original: str, bad_output: str, error: str) -> str:
        return (
            "Your previous session-reviewer output failed validation. Correct "
            "and reprint between the markers.\n\n"
            "--- Original task ---\n"
            f"{original}\n\n"
            "--- Previous output (failed) ---\n"
            f"{bad_output}\n\n"
            "--- Validation error ---\n"
            f"{error}\n\n"
            f"Reprint the corrected document between {REVIEW_DELIM_START} "
            f"and {REVIEW_DELIM_END}. No commentary outside the markers."
        )

    # ---- merge + save ----

    def _merge(self, facts, analysis: dict[str, Any]) -> SessionReviewV1:
        return SessionReviewV1.model_validate({
            "schema": SCHEMA_VERSION,
            "session_id": facts.session_id,
            "project": facts.project,
            "role": facts.role,
            "date": facts.date,
            "timestamp": datetime.now(tz=timezone.utc),
            "metrics": facts.metrics.model_dump(),
            "artifacts": facts.artifacts.model_dump(),
            "links": facts.links.model_dump(exclude_none=True),
            "summary": analysis["summary"],
            "observations": analysis.get("observations") or [],
            "hypothesis_verdicts": analysis.get("hypothesis_verdicts") or [],
            "proposal_actions": analysis.get("proposal_actions") or [],
            "self_problems": analysis.get("self_problems") or [],
        })

    def _validate_integrity(
        self, review: SessionReviewV1, expected_session_id: str
    ) -> None:
        if review.session_id != expected_session_id:
            raise ValueError(
                f"session_id mismatch: review has {review.session_id!r}, "
                f"expected {expected_session_id!r}"
            )
        active_ids = {h.id for h in self.hypotheses.list_active()}
        verdict_ids = {v.hyp_id for v in review.hypothesis_verdicts}
        missing = active_ids - verdict_ids
        if missing:
            raise ValueError(
                "hypothesis_verdicts missing entries for active HYPs: "
                f"{sorted(missing)}"
            )

    def _save(self, review: SessionReviewV1) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / f"{review.session_id}.md"
        body = self._render_body(review)
        dump(review, path, body=body)
        return path

    @staticmethod
    def _render_body(review: SessionReviewV1) -> str:
        lines: list[str] = [
            f"# Session Review: {review.session_id}",
            "",
            f"**Role:** {review.role}  ",
            f"**Date:** {review.date.isoformat()}  ",
            f"**Project:** {review.project}",
            "",
            "## Summary",
            "",
            review.summary,
            "",
        ]
        if review.observations:
            lines.append("## Observations")
            lines.append("")
            for obs in review.observations:
                lines.append(f"- {obs}")
            lines.append("")
        if review.hypothesis_verdicts:
            lines.append("## Hypothesis verdicts")
            lines.append("")
            for v in review.hypothesis_verdicts:
                lines.append(
                    f"- **{v.hyp_id}** — {v.verdict} "
                    f"({v.recommendation}): {v.evidence}"
                )
            lines.append("")
        if review.proposal_actions:
            lines.append("## Proposal actions")
            lines.append("")
            for a in review.proposal_actions:
                lines.append(f"- {a.action} {a.prop_id}")
            lines.append("")
        if review.artifacts.files_changed:
            lines.append("## Files changed")
            lines.append("")
            for f in review.artifacts.files_changed:
                lines.append(f"- {f}")
            lines.append("")
        if review.artifacts.commits:
            lines.append("## Commits")
            lines.append("")
            for c in review.artifacts.commits:
                lines.append(f"- {c}")
            lines.append("")
        return "\n".join(lines)
