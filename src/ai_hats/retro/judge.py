"""JudgeRunner — orchestrate bundle → judge sub-agent → validated JudgeRetroV1.

Pipeline:
1. Resolve or auto-create bundle
2. Build structured prompt with session context
3. Spawn judge sub-agent via SubAgentRunner
4. Extract markdown between BEGIN_JUDGE_RETRO/END_JUDGE_RETRO delimiters
5. Validate via HATS-051 loader; one retry with correction prompt on failure
6. Save validated output to .agent/retrospectives/judge/<date>-judge-NNN.md
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import ValidationError

from .bundle import BundleV1
from .bundles import BundleManager
from .judge_retro import JudgeRetroV1
from .loader import parse
from .migrations import migrate_to_latest
from .writer import dump

if TYPE_CHECKING:
    from ..runtime import SubAgentRunner

JUDGE_DELIM_START = "BEGIN_JUDGE_RETRO"
JUDGE_DELIM_END = "END_JUDGE_RETRO"
JUDGE_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-judge-(\d{3})\.md$")
SESSION_PREFIX = "session_"


class JudgeValidationError(Exception):
    """Judge sub-agent output failed schema validation after all retries."""


class JudgeRunner:
    """Orchestrates bundle → judge sub-session → validated JudgeRetroV1."""

    def __init__(
        self,
        project_dir: Path,
        *,
        subagent_runner: "SubAgentRunner | None" = None,
        bundle_manager: BundleManager | None = None,
    ) -> None:
        self.project_dir = project_dir
        self.judge_dir = project_dir / ".agent" / "retrospectives" / "judge"
        self.gitlog_dir = project_dir / ".gitlog"
        self._subagent_runner = subagent_runner
        self.bundles = bundle_manager or BundleManager(project_dir)

    # --- public API ---

    def judge(
        self,
        *,
        bundle_id: str | None = None,
        session_ids: list[str] | None = None,
        last_n: int | None = None,
        focus: str | None = None,
        max_retries: int = 1,
    ) -> Path:
        """End-to-end judge run. Returns the path to the saved judge retro.

        `focus` is the lens for THIS judge run only — it does not modify the
        bundle. The same bundle can be judged repeatedly with different lenses.
        """
        bundle = self._resolve_bundle(
            bundle_id=bundle_id,
            session_ids=session_ids,
            last_n=last_n,
        )
        prompt = self._build_judge_prompt(bundle, focus=focus)
        model, body = self._run_and_validate(bundle, prompt, max_retries)
        return self._save(model, body)

    # --- bundle resolution ---

    def _resolve_bundle(
        self,
        *,
        bundle_id: str | None,
        session_ids: list[str] | None,
        last_n: int | None,
    ) -> BundleV1:
        if bundle_id:
            return self.bundles.get(bundle_id)
        if session_ids:
            return self.bundles.create(session_ids)
        if last_n:
            return self.bundles.create_from_last(last_n)
        raise ValueError(
            "Must provide one of: bundle_id, session_ids, last_n"
        )

    # --- prompt building ---

    def _build_judge_prompt(self, bundle: BundleV1, *, focus: str | None = None) -> str:
        sections: list[str] = []
        sections.append(
            "You are running as the judge role over a bundle of sessions.\n"
            "Produce a hats-judge-retro/v1 markdown document and print it "
            f"between the {JUDGE_DELIM_START} and {JUDGE_DELIM_END} markers "
            "below. The document MUST start with YAML frontmatter (`---`)."
        )
        sections.append(f"Bundle: {bundle.bundle_id}")
        sections.append(f"Project: {bundle.project}")
        if focus:
            sections.append(f"Focus: {focus}")
        if bundle.notes:
            sections.append(f"Notes: {bundle.notes}")

        sections.append("Sessions in this bundle:")
        for sid in bundle.session_ids:
            section = self._render_session_section(sid)
            sections.append(section)

        sections.append(
            "Required output: print exactly one judge retrospective markdown\n"
            f"document between {JUDGE_DELIM_START} and {JUDGE_DELIM_END}.\n"
            "Required frontmatter fields: schema=hats-judge-retro/v1, judge_run_id,\n"
            f"project, date, bundle_id={bundle.bundle_id}, findings (min 1).\n"
            "Each finding must include id (F1, F2, ...), title, category,\n"
            "severity, root_cause, and at least one evidence entry with session_id."
        )
        sections.append(f"\n{JUDGE_DELIM_START}\n... your judge retro here ...\n{JUDGE_DELIM_END}\n")
        return "\n\n".join(sections)

    def _find_session_retro(self, session_id: str) -> Path | None:
        """Locate a session retro for `session_id`, preferring llm > programmatic."""
        base = self.project_dir / ".agent" / "retrospectives" / "sessions"
        for mode in ("llm", "programmatic"):
            path = base / mode / f"{session_id}.md"
            if path.exists():
                return path
        return None

    def _render_session_section(self, session_id: str) -> str:
        sdir = self.gitlog_dir / f"{SESSION_PREFIX}{session_id}"
        parts = [f"### Session {session_id}"]
        # Inline session retro if it exists. Prefer llm version over programmatic
        # because it has narrative content the judge can actually use.
        retro_path = self._find_session_retro(session_id)
        if retro_path is not None:
            parts.append(f"Session retro:\n```\n{retro_path.read_text()}\n```")
        # Inline metrics
        metrics_path = sdir / "metrics.json"
        if metrics_path.exists():
            try:
                metrics = json.loads(metrics_path.read_text())
                parts.append(
                    f"metrics.json:\n```json\n{json.dumps(metrics, indent=2)}\n```"
                )
            except json.JSONDecodeError:
                pass
        # Inline audit (truncated)
        audit_path = sdir / "audit.md"
        if audit_path.exists():
            audit_text = audit_path.read_text()
            if len(audit_text) > 8000:
                audit_text = audit_text[:8000] + "\n... (truncated)"
            parts.append(f"audit.md:\n```\n{audit_text}\n```")
        return "\n\n".join(parts)

    # --- run + validate + retry ---

    def _run_and_validate(
        self, bundle: BundleV1, prompt: str, max_retries: int
    ) -> tuple[JudgeRetroV1, str]:
        runner = self._get_runner()
        attempt = 0
        current_prompt = prompt
        last_error: Exception | None = None
        last_transcript = ""
        last_session_dir: Path | None = None
        while True:
            session = runner.run(
                role_name="judge",
                task=current_prompt,
                isolation_mode="discard",
            )
            last_session_dir = session.session_dir
            transcript_path = session.session_dir / "transcript.txt"
            transcript = transcript_path.read_text() if transcript_path.exists() else ""
            last_transcript = transcript
            md = self._extract_markdown(transcript)
            try:
                raw, body = parse(md)
                migrated = migrate_to_latest(raw)
                model = JudgeRetroV1.model_validate(migrated)
                return model, body
            except (ValidationError, ValueError, yaml.YAMLError) as e:
                last_error = e
                if attempt >= max_retries:
                    break
                attempt += 1
                current_prompt = self._retry_prompt(prompt, md, str(e))
        raise JudgeValidationError(
            f"Judge output failed validation after {attempt + 1} attempt(s): "
            f"{last_error}\n"
            f"Last transcript: {last_session_dir}/transcript.txt\n"
            f"Last extracted markdown:\n{last_transcript[:500]}"
        )

    def _get_runner(self) -> "SubAgentRunner":
        if self._subagent_runner is not None:
            return self._subagent_runner
        from ..runtime import SubAgentRunner

        return SubAgentRunner(self.project_dir)

    def _extract_markdown(self, transcript: str) -> str:
        if not transcript:
            return ""
        s = transcript.find(JUDGE_DELIM_START)
        if s >= 0:
            e = transcript.find(JUDGE_DELIM_END, s + len(JUDGE_DELIM_START))
            if e > s:
                return transcript[s + len(JUDGE_DELIM_START):e].strip()
        # Fallback: scan for frontmatter start that names the judge schema
        fm = transcript.find("---\nschema: hats-judge-retro")
        if fm >= 0:
            return transcript[fm:]
        return transcript

    def _retry_prompt(self, original: str, bad_output: str, error: str) -> str:
        return (
            "The previous attempt to produce a judge retrospective failed schema "
            "validation. You must correct the errors and produce a new, complete "
            f"output between {JUDGE_DELIM_START} and {JUDGE_DELIM_END} markers.\n\n"
            "--- Original task ---\n"
            f"{original}\n\n"
            "--- Previous output (this failed) ---\n"
            f"{bad_output}\n\n"
            "--- Validation error ---\n"
            f"{error}\n\n"
            f"Produce a corrected hats-judge-retro/v1 document. Print it between "
            f"{JUDGE_DELIM_START} and {JUDGE_DELIM_END} markers. Every field that "
            "caused a validation error must be fixed. Do not add commentary outside "
            "the markers."
        )

    # --- save ---

    def _save(self, model: JudgeRetroV1, body: str) -> Path:
        self.judge_dir.mkdir(parents=True, exist_ok=True)
        path = self._next_judge_filename(model.date)
        dump(model, path, body=body)
        return path

    def _next_judge_filename(self, today: date | None = None) -> Path:
        today = today or datetime.now(timezone.utc).date()
        today_str = today.isoformat()
        max_seq = 0
        if self.judge_dir.exists():
            for entry in self.judge_dir.iterdir():
                m = JUDGE_FILE_RE.match(entry.name)
                if m and m.group(1) == today_str:
                    max_seq = max(max_seq, int(m.group(2)))
        return self.judge_dir / f"{today_str}-judge-{max_seq + 1:03d}.md"
