"""Background-spawn entry point for `ai-hats reflect session --background`.

Invoked as:
    python -m ai_hats.cli.reflect_session_main <session_id> [max_retries]

Runs :class:`SessionReviewRunner` in-process so the parent's Popen captures
all output (<ai_hats_dir>/sessions/runs/session_<id>/retro.log). After the runner returns or
raises, runs a pure-Python harness check that files a single meta-proposal
when the persisted artifact is missing/incomplete — single ownership of the
failure-proposal lives here (not in the runner) to avoid double-fire.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from ai_hats_core.layout import ProjectLayout

import yaml

from ..harness.errors import HarnessReliabilityError
from ..pipeline import run_pipeline
from ..pipeline_catalog import REFLECT_SESSION
from ..session_policy import ReflectSessionRunParams, SessionReviewOutcome
from ..retro.session_review_runner import SessionReviewError

logger = logging.getLogger(__name__)


# HATS-378: meta-PROP targets. session-reviewer = role's own output failed
# validation (schema, empty frontmatter). harness-incident = the harness
# layer detected a failure independent of the role's logic
# (subprocess timeout, zero-output silent run).
TARGET_SESSION_REVIEWER = "session-reviewer"
TARGET_HARNESS_INCIDENT = "harness-incident"


def main() -> int:
    if len(sys.argv) < 2:
        print(
            "usage: reflect_session_main <session_id> [max_retries]",
            file=sys.stderr,
        )
        return 2
    session_id = sys.argv[1]
    max_retries = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    from ._entry import resolve_project

    layout = resolve_project().layout

    return run_session_review(session_id, max_retries, layout)


def run_session_review(session_id: str, max_retries: int, layout: ProjectLayout) -> int:
    """Run the session-reviewer pipeline in-process; return an exit code.

    Extracted from ``main()`` (HATS-1402) so a caller like
    ``MaybeSpawnSessionReviewer``'s ``background: false`` branch can run it
    synchronously in-process instead of only via the CLI subprocess.
    """
    project_dir = layout.root
    retros = layout.sessions.retros
    runner_error: str | None = None
    harness_error: HarnessReliabilityError | None = None
    saved_path: Path | None = None
    try:
        result = run_pipeline(
            REFLECT_SESSION,
            ReflectSessionRunParams(
                layout=layout,
                session_id=session_id,
                max_retries=max_retries,
            ),
        )
        saved_path = SessionReviewOutcome.of(result).review_path
    except HarnessReliabilityError as exc:
        # HATS-378: harness-layer failure (timeout, zero-output guard) →
        # file under target=harness-incident, NOT session-reviewer.
        harness_error = exc
        print(
            f"harness incident for {session_id}: {exc}",
            file=sys.stderr,
        )
    except SessionReviewError as exc:
        runner_error = str(exc)
        print(
            f"session-reviewer failed for {session_id}: {exc}",
            file=sys.stderr,
        )

    # HATS-1369 / HATS-1422: parse the doc ONCE — shared by the harvest below and
    # _harness_check, instead of each re-reading/re-parsing it independently.
    raw, parse_issues = _load_review_doc(_review_doc_path(retros, session_id))

    # Harvest whatever verdicts the doc carries into validation_log,
    # independent of _harness_check's full-active-coverage gate below or
    # harness-reliability failures above (HATS-1422).
    persisted = _maybe_harvest_verdicts(project_dir, session_id, raw)

    if harness_error is not None:
        _file_meta_proposal(
            project_dir,
            session_id,
            issues=[f"harness: {harness_error}"],
            target=TARGET_HARNESS_INCIDENT,
        )
        if persisted:
            print(f"harvested {len(persisted)} verdict(s): {persisted}")
        return 2

    issues = _harness_check(project_dir, session_id, runner_error, raw, parse_issues)
    if issues:
        _file_meta_proposal(
            project_dir,
            session_id,
            issues,
            target=TARGET_SESSION_REVIEWER,
        )
        if persisted:
            print(f"harvested {len(persisted)} verdict(s): {persisted}")
        return 2
    if saved_path is not None:
        print(f"session-reviewer saved to {saved_path}")
    if persisted:
        print(f"harvested {len(persisted)} verdict(s): {persisted}")
    return 0


# ---- shared doc parse (HATS-1369: one parse, shared by harness_check + harvest) ----

_MISSING_ISSUE = "output file missing or empty"


def _review_doc_path(retros: Path, session_id: str) -> Path:
    return retros / "sessions" / f"{session_id}.md"


def _load_review_doc(out_path: Path) -> tuple[dict | None, list[str]]:
    """Read + parse the review doc's frontmatter into a mapping.

    Returns ``(mapping, issues)``: ``mapping`` is ``None`` when the file is
    missing/empty, its frontmatter fails to parse, or the frontmatter isn't a
    YAML mapping — ``issues`` names which (exactly one entry). Shared by
    ``_harness_check`` and ``_maybe_harvest_verdicts`` so the doc is parsed once.
    """
    if not out_path.exists() or out_path.stat().st_size == 0:
        return None, [_MISSING_ISSUE]
    try:
        raw = yaml.safe_load(_extract_frontmatter(out_path.read_text()))
    except yaml.YAMLError as e:
        return None, [f"frontmatter parse error: {e}"]
    except (OSError, ValueError) as e:
        return None, [f"output unreadable: {e}"]
    if not isinstance(raw, dict):
        return None, ["frontmatter is not a YAML mapping"]
    return raw, []


# ---- harness check (pure-Python, no LLM) ----


def _harness_check(
    project_dir: Path,
    session_id: str,
    runner_error: str | None,
    raw: dict | None,
    parse_issues: list[str],
) -> list[str]:
    """Return a list of issue strings; empty means pass.

    ``raw``/``parse_issues`` come from ONE ``_load_review_doc`` call made by
    the caller (``main()``) — this function does not re-read/re-parse.
    """
    if raw is None:
        issues = list(parse_issues)
        if runner_error and issues == [_MISSING_ISSUE]:
            issues = [f"{_MISSING_ISSUE} (runner: {runner_error[:200]})"]
        return issues

    issues = []
    summary = raw.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        issues.append("`summary` missing or empty")

    verdicts = raw.get("hypothesis_verdicts") or []
    if not isinstance(verdicts, list):
        issues.append("`hypothesis_verdicts` is not a list")
        verdicts = []

    try:
        active_ids = _load_active_hyp_ids(project_dir, session_id)
    except Exception as e:  # noqa: BLE001 — observability over correctness
        issues.append(f"could not enumerate active HYPs: {e}")
        active_ids = set()

    if active_ids:
        verdict_ids = {v.get("hyp_id") for v in verdicts if isinstance(v, dict) and v.get("hyp_id")}
        missing = active_ids - verdict_ids
        if missing:
            issues.append("missing verdicts for active HYPs: " + ", ".join(sorted(missing)))

    if runner_error:
        # File otherwise valid but runner raised mid-flight — surface so the
        # inbox shows the warning.
        issues.append(f"runner reported: {runner_error[:200]}")
    return issues


def _extract_frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    rest = text[len("---\n") :]
    end = rest.find("\n---\n")
    if end == -1:
        if rest.endswith("\n---"):
            return rest[: -len("\n---")]
        raise ValueError("malformed frontmatter: missing closing ---")
    return rest[:end]


def _load_active_hyp_ids(project_dir: Path, session_id: str) -> set[str]:
    from ..rack_workspace import active_hypotheses, created_at_or_before, rack_workspace
    from ..retro.window import session_cut

    ws = rack_workspace(project_dir)
    every = active_hypotheses(ws)
    kept = created_at_or_before(every, session_cut(project_dir, session_id))
    return {h.id for h in kept}


# ---- verdict harvest (HATS-1369) ----


def _maybe_harvest_verdicts(project_dir: Path, session_id: str, raw: dict | None) -> list[str]:
    """Harvest whatever verdicts ``raw`` carries; ``[]`` when ``raw`` is
    ``None`` (doc missing/unparseable — nothing to harvest) or its
    ``hypothesis_verdicts`` isn't a list. ``raw`` comes from the caller's
    single ``_load_review_doc`` call — this does not re-read/re-parse."""
    if raw is None:
        return []
    verdicts = raw.get("hypothesis_verdicts")
    if not isinstance(verdicts, list):
        return []
    return _harvest_verdicts(project_dir, session_id, verdicts)


def _harvest_verdicts(project_dir: Path, session_id: str, verdicts: list) -> list[str]:
    """Persist each non-``n/a`` verdict into its HYP's ``validation_log``.

    ``session_id`` is this function's OWN argument — the session under
    review, never a judge/session-reviewer's own id — so quorum_autoclose's
    independent-session count attributes correctly. ``evidence``/
    ``recommendation`` are copied verbatim, never reformulated. A verdict
    without ``hyp_id`` or with ``verdict: n/a`` is skipped; one
    ``append_verdict`` failure (unknown/inactive hyp_id, race) is logged and
    does not abort the rest. Returns the hyp_ids actually persisted.
    """
    from ..rack_workspace import SESSION_REVIEWER_ACTOR, append_verdict, rack_workspace

    ws = rack_workspace(project_dir)
    now = datetime.now(timezone.utc)
    persisted: list[str] = []
    for v in verdicts:
        if not isinstance(v, dict):
            continue
        hyp_id = v.get("hyp_id")
        verdict = v.get("verdict")
        if not hyp_id or verdict is None or verdict == "n/a":
            continue
        entry = {
            "verdict": verdict,
            "evidence": v.get("evidence"),
            "recommendation": v.get("recommendation"),
            "session_id": session_id,
            "date": v.get("date") or now.date().isoformat(),
            "timestamp": v.get("timestamp") or now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        try:
            append_verdict(ws, hyp_id, entry, caller_cwd=project_dir, actor=SESSION_REVIEWER_ACTOR)
        except (Exception, KeyboardInterrupt):
            logger.warning(
                "verdict harvest failed for %s (session %s)", hyp_id, session_id, exc_info=True
            )
            continue
        persisted.append(hyp_id)
    return persisted


# ---- meta-proposal filing ----


def _file_meta_proposal(
    project_dir: Path,
    session_id: str,
    issues: list[str],
    *,
    target: str = TARGET_SESSION_REVIEWER,
) -> None:
    from ..rack_workspace import create_proposal, proposals, rack_workspace

    ws = rack_workspace(project_dir)

    # De-dup: skip if a process proposal with the SAME target already exists for
    # this failed_session_id (distinct targets coexist — both facets get filed).
    for existing in proposals(ws, category="process", target=target):
        if existing.failed_session_id == session_id:
            print(
                f"[harness] meta-proposal already filed for {session_id} "
                f"(target={target}): {existing.id}",
                file=sys.stderr,
            )
            return

    title, description, rationale = _build_meta_proposal_body(
        session_id,
        issues,
        target,
    )
    try:
        prop_id = create_proposal(
            ws,
            title=title,
            category="process",
            target=target,
            description=description,
            rationale=rationale,
            failed_session_id=session_id,
        )
        print(f"[harness] filed meta-proposal: {prop_id}", file=sys.stderr)
    except (FileExistsError, OSError) as e:
        print(
            f"[harness] failed to file meta-proposal for {session_id}: {e}",
            file=sys.stderr,
        )


def _build_meta_proposal_body(
    session_id: str,
    issues: list[str],
    target: str,
) -> tuple[str, str, str]:
    """Build (title, description, rationale) appropriate for the target."""
    joined = "; ".join(issues)
    if target == TARGET_HARNESS_INCIDENT:
        title = f"harness incident: {session_id}"[:200]
        description = (f"Harness reliability failure for session {session_id}. Details: {joined}")[
            :1000
        ]
        rationale = (
            "Runtime safety net: harness detected a failure independent "
            "of the reporting role (subprocess timeout or silent "
            "zero-output run). Investigate the harness/subprocess "
            "plumbing rather than the role itself."
        )
        return title, description, rationale
    # Default: session-reviewer target.
    title = f"session-reviewer incomplete: {session_id}"[:200]
    description = (
        "Harness check detected incomplete session-reviewer output for "
        f"{session_id}. Issues: {joined}"
    )[:1000]
    rationale = (
        "Runtime safety net: harness check detected incomplete review "
        "output. Re-run with `ai-hats reflect session --session "
        f"{session_id}` after addressing the cause."
    )
    return title, description, rationale


if __name__ == "__main__":
    sys.exit(main())
