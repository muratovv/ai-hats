"""ReflectSessionRunner — per-session judge run (HATS-210).

Two-layer no-silent-failure protection:

  1. **LLM-driven** (in-prompt + skill text): on meta-problems the judge
     creates a proposal via `ai-hats task proposal create` and references it
     in `self_problems`.
  2. **Runtime-driven** (this module): after the sub-agent exits, parent
     reads the output file and validates it against ReflectSessionV1.
     If absent or invalid → programmatically creates a meta-proposal with
     `category=process`, `target=reflect-session`, `failed_session_id=<sid>`.

Both layers run; the runtime layer is the backstop when the LLM also fails
at filing its own meta-proposal.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import ValidationError

from ..hypothesis import (
    HypothesisStore,
    Proposal,
    ProposalStore,
    next_proposal_id,
)
from .loader import parse
from .reflect_session_schema import ReflectSessionV1

if TYPE_CHECKING:
    from ..runtime import SubAgentRunner

logger = logging.getLogger(__name__)

REFLECT_DELIM_START = "BEGIN_REFLECT_SESSION_RETRO"
REFLECT_DELIM_END = "END_REFLECT_SESSION_RETRO"


class ReflectSessionError(Exception):
    """Reflect-session run failed; meta-proposal already filed."""


class ReflectSessionRunner:
    """Run reflect-session sub-agent on a single session and validate output."""

    def __init__(
        self,
        project_dir: Path,
        *,
        subagent_runner: "SubAgentRunner | None" = None,
    ) -> None:
        self.project_dir = project_dir
        self.out_dir = (
            project_dir / ".agent" / "retrospectives" / "reflect-session"
        )
        self.gitlog_dir = project_dir / ".gitlog"
        self.hypotheses = HypothesisStore(project_dir / ".agent" / "hypotheses")
        self.proposals = ProposalStore(
            project_dir / ".agent" / "backlog" / "proposals"
        )
        self._subagent_runner = subagent_runner

    # ---- public API ----

    def run(self, session_id: str, *, max_retries: int = 1) -> Path:
        """Run reflect-session on `session_id`. Returns path to the saved retro.

        On failure of any kind, programmatically files a meta-proposal and
        re-raises ReflectSessionError (caller may suppress to stay non-fatal).
        """
        try:
            prompt = self._build_prompt(session_id)
            model, body = self._run_and_validate(prompt, session_id, max_retries)
            return self._save(model, body, session_id)
        except Exception as exc:  # noqa: BLE001 — runtime-level safety net
            error_msg = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "reflect-session failed for %s: %s", session_id, error_msg,
                exc_info=True,
            )
            self._spawn_meta_proposal(session_id, error_msg)
            raise ReflectSessionError(error_msg) from exc

    # ---- prompt ----

    def _build_prompt(self, session_id: str) -> str:
        sections: list[str] = []
        sections.append(
            "You are running as the reflect-session role over ONE session.\n"
            f"Session id: {session_id}\n"
            "Produce a hats-reflect-session/v1 markdown document and print it "
            f"between {REFLECT_DELIM_START} and {REFLECT_DELIM_END}. "
            "The document MUST start with YAML frontmatter (`---`)."
        )
        sections.append(self._render_active_hypotheses())
        sections.append(self._render_open_proposals())
        sections.append(self._render_session_evidence(session_id))
        sections.append(
            "## Output requirements (STRICT — extras are rejected)\n\n"
            "Frontmatter (YAML) MUST contain:\n"
            "  schema: hats-reflect-session/v1\n"
            f"  session_id: {session_id}\n"
            "  timestamp: <UTC ISO-8601>\n"
            "  hypothesis_verdicts:\n"
            "    - hyp_id: HYP-NNN\n"
            "      verdict: confirmed | refuted | inconclusive | n/a\n"
            "      evidence: <one-line citation>\n"
            "      recommendation: close_confirmed | close_refuted | "
            "keep | extend_window\n"
            "    (one entry PER active HYP listed above; do not skip)\n"
            "  proposal_actions:\n"
            "    - action: created | voted\n"
            "      prop_id: PROP-NNN\n"
            "  self_problems: [PROP-NNN, ...]\n\n"
            "Before listing a proposal action, USE THE CLI to materialize it:\n"
            "  ai-hats task proposal create --title ... --category ... --target ... "
            "--description ... --rationale ... --session " + session_id + "\n"
            "  ai-hats task proposal vote --prop PROP-NNN --session " + session_id + " "
            "--reasoning ...\n"
            "If you cannot follow the format or hit a meta-problem, file:\n"
            "  ai-hats task proposal create --category process --target "
            "reflect-session \\\n"
            "    --title <short> --description <what failed> --rationale <why> "
            "--session " + session_id + "\n"
            "and reference the resulting PROP-NNN in `self_problems`. "
            "NEVER silently drop entries."
        )
        sections.append(
            f"\n{REFLECT_DELIM_START}\n... your reflect-session retro here "
            f"...\n{REFLECT_DELIM_END}\n"
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
        return "\n".join(lines)

    def _render_open_proposals(self) -> str:
        open_props = self.proposals.filter(status="open")
        if not open_props:
            return (
                "## Open proposals\n\n(inbox empty — create new ones with "
                "`ai-hats task proposal create` if you spot improvements)"
            )
        lines = [
            "## Open proposals (vote on similar; create only if novel)"
        ]
        for p in open_props:
            lines.append(
                f"- **{p.id}** [{p.category}/{p.target}] {p.title}\n"
                f"  description: {p.description}"
            )
        return "\n".join(lines)

    def _render_session_evidence(self, session_id: str) -> str:
        sdir = self.gitlog_dir / f"session_{session_id}"
        parts = [f"## Session evidence for {session_id}"]
        # Session retro (if available)
        retro = self._find_session_retro(session_id)
        if retro is not None:
            parts.append(f"Session retro:\n```\n{retro.read_text()}\n```")
        # Metrics
        metrics_path = sdir / "metrics.json"
        if metrics_path.exists():
            try:
                metrics = json.loads(metrics_path.read_text())
                parts.append(
                    f"metrics.json:\n```json\n"
                    f"{json.dumps(metrics, indent=2)}\n```"
                )
            except json.JSONDecodeError:
                pass
        # Audit (truncated)
        audit_path = sdir / "audit.md"
        if audit_path.exists():
            audit_text = audit_path.read_text()
            if len(audit_text) > 8000:
                audit_text = audit_text[:8000] + "\n... (truncated)"
            parts.append(f"audit.md:\n```\n{audit_text}\n```")
        return "\n\n".join(parts)

    def _find_session_retro(self, session_id: str) -> Path | None:
        base = self.project_dir / ".agent" / "retrospectives" / "sessions"
        p = base / f"{session_id}.md"
        if p.exists():
            return p
        return None

    # ---- run + validate ----

    def _get_runner(self) -> "SubAgentRunner":
        if self._subagent_runner is not None:
            return self._subagent_runner
        from ..runtime import SubAgentRunner
        return SubAgentRunner(self.project_dir)

    def _reflect_model(self) -> str:
        """Read `feedback.session_retro.reflect_model` from ai-hats.yaml.

        Returns empty string when unset or config missing — SubAgentRunner
        treats falsy as "no override" and the provider CLI's default applies.
        """
        from ..models import ProjectConfig

        cfg_path = self.project_dir / "ai-hats.yaml"
        if not cfg_path.exists():
            return ""
        try:
            cfg = ProjectConfig.from_yaml(cfg_path)
        except Exception:
            return ""
        return cfg.feedback.session_retro.reflect_model or ""

    def _run_and_validate(
        self, prompt: str, session_id: str, max_retries: int,
    ) -> tuple[ReflectSessionV1, str]:
        runner = self._get_runner()
        reflect_model = self._reflect_model()
        attempt = 0
        current_prompt = prompt
        last_error: Exception | None = None
        last_md = ""
        while True:
            session = runner.run(
                role_name="reflect-session",
                task=current_prompt,
                model=reflect_model,
                # NONE: sub-agent needs access to real .agent/ (gitignored,
                # invisible inside a git worktree) so its CLI calls land in
                # the project's hypothesis backlog, not a throwaway dir.
                # Trust model: role injection forbids non-CLI mutations.
                isolation_mode="none",
            )
            transcript_path = session.session_dir / "transcript.txt"
            transcript = (
                transcript_path.read_text() if transcript_path.exists() else ""
            )
            md = self._extract_markdown(transcript)
            last_md = md
            try:
                raw, body = parse(md)
                model = ReflectSessionV1.model_validate(raw)
                self._validate_integrity(model, session_id)
                return model, body
            except (ValidationError, ValueError, yaml.YAMLError) as e:
                last_error = e
                if attempt >= max_retries:
                    break
                attempt += 1
                current_prompt = self._retry_prompt(prompt, md, str(e))
        raise ValueError(
            f"reflect-session output failed validation after "
            f"{attempt + 1} attempt(s): {last_error}\n"
            f"Last extracted markdown (truncated):\n{last_md[:500]}"
        )

    def _extract_markdown(self, transcript: str) -> str:
        if not transcript:
            return ""
        s = transcript.find(REFLECT_DELIM_START)
        if s >= 0:
            e = transcript.find(REFLECT_DELIM_END, s + len(REFLECT_DELIM_START))
            if e > s:
                return transcript[s + len(REFLECT_DELIM_START):e].strip()
        # Fallback — frontmatter sniffing
        fm = transcript.find("---\nschema: hats-reflect-session")
        if fm >= 0:
            return transcript[fm:]
        return transcript

    def _validate_integrity(
        self, model: ReflectSessionV1, expected_session_id: str
    ) -> None:
        if model.session_id != expected_session_id:
            raise ValueError(
                f"session_id mismatch: output has {model.session_id!r}, "
                f"expected {expected_session_id!r}"
            )
        active_ids = {h.id for h in self.hypotheses.list_active()}
        verdict_ids = {v.hyp_id for v in model.hypothesis_verdicts}
        missing = active_ids - verdict_ids
        if missing:
            raise ValueError(
                "hypothesis_verdicts missing entries for active HYPs: "
                f"{sorted(missing)}"
            )

    def _retry_prompt(self, original: str, bad_output: str, error: str) -> str:
        return (
            "Your previous reflect-session output failed validation. Correct "
            "and reprint between the markers.\n\n"
            "--- Original task ---\n"
            f"{original}\n\n"
            "--- Previous output (failed) ---\n"
            f"{bad_output}\n\n"
            "--- Validation error ---\n"
            f"{error}\n\n"
            f"Reprint the corrected document between {REFLECT_DELIM_START} "
            f"and {REFLECT_DELIM_END}. No commentary outside the markers."
        )

    # ---- save ----

    def _save(
        self, model: ReflectSessionV1, body: str, session_id: str,
    ) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / f"{session_id}.md"
        fm_dict = model.model_dump(by_alias=True, mode="json", exclude_none=True)
        fm = yaml.safe_dump(fm_dict, sort_keys=False, allow_unicode=True)
        path.write_text(f"---\n{fm}---\n{body}")
        return path

    # ---- runtime safety net: meta-proposal ----

    def _spawn_meta_proposal(self, session_id: str, error_msg: str) -> Path:
        prop_id = next_proposal_id(self.proposals.dir)
        title = (
            f"reflect-session failed on {session_id}"[:200]
        )
        prop = Proposal(
            id=prop_id,
            created=datetime.now(tz=timezone.utc),
            title=title,
            category="process",
            target="reflect-session",
            description=(
                f"Reflect-session run failed with: {error_msg[:400]}. "
                "Re-run with `ai-hats reflect session --session "
                f"{session_id}` after addressing the cause."
            ),
            rationale=(
                "Runtime safety net: judge process failed and did not file "
                "its own meta-proposal. This proposal exists so the failure "
                "is visible in the inbox."
            ),
            failed_session_id=session_id,
        )
        return self.proposals.create(prop)
