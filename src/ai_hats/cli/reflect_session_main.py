"""Background-spawn entry point for `ai-hats reflect session --background`.

Invoked as:
    python -m ai_hats.cli.reflect_session_main <session_id> [max_retries]

Runs :class:`SessionReviewRunner` in-process so the parent's Popen captures
all output (.gitlog/session_<id>/retro.log). After the runner returns or
raises, runs a pure-Python harness check that files a single meta-proposal
when the persisted artifact is missing/incomplete — single ownership of the
failure-proposal lives here (not in the runner) to avoid double-fire.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from pydantic import ValidationError

from ..hypothesis import HypothesisStore, Proposal, ProposalStore, next_proposal_id
from ..pipeline.harness import PipelineHarness
from ..retro.session_review_runner import SessionReviewError


def main() -> int:
    if len(sys.argv) < 2:
        print(
            "usage: reflect_session_main <session_id> [max_retries]",
            file=sys.stderr,
        )
        return 2
    session_id = sys.argv[1]
    max_retries = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    project_dir = Path.cwd()

    runner_error: str | None = None
    saved_path: Path | None = None
    try:
        with PipelineHarness("reflect-session", project_dir) as h:
            final = h.run({
                "session_id": session_id,
                "project_dir": project_dir,
                "max_retries": max_retries,
            })
            saved_path = final.get("review_path")
    except SessionReviewError as exc:
        runner_error = str(exc)
        print(
            f"session-reviewer failed for {session_id}: {exc}",
            file=sys.stderr,
        )

    issues = _harness_check(project_dir, session_id, runner_error)
    if issues:
        _file_meta_proposal(project_dir, session_id, issues)
        return 2
    if saved_path is not None:
        print(f"session-reviewer saved to {saved_path}")
    return 0


# ---- harness check (pure-Python, no LLM) ----


def _harness_check(
    project_dir: Path, session_id: str, runner_error: str | None,
) -> list[str]:
    """Return a list of issue strings; empty means pass."""
    issues: list[str] = []
    out_path = (
        project_dir / ".agent" / "retrospectives" / "sessions"
        / f"{session_id}.md"
    )
    if not out_path.exists() or out_path.stat().st_size == 0:
        msg = "output file missing or empty"
        if runner_error:
            msg += f" (runner: {runner_error[:200]})"
        issues.append(msg)
        return issues

    try:
        raw = yaml.safe_load(_extract_frontmatter(out_path.read_text()))
        if not isinstance(raw, dict):
            issues.append("frontmatter is not a YAML mapping")
            return issues
    except yaml.YAMLError as e:
        issues.append(f"frontmatter parse error: {e}")
        return issues
    except (OSError, ValueError) as e:
        issues.append(f"output unreadable: {e}")
        return issues

    summary = raw.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        issues.append("`summary` missing or empty")

    verdicts = raw.get("hypothesis_verdicts") or []
    if not isinstance(verdicts, list):
        issues.append("`hypothesis_verdicts` is not a list")
        verdicts = []

    try:
        active_ids = _load_active_hyp_ids(project_dir)
    except Exception as e:  # noqa: BLE001 — observability over correctness
        issues.append(f"could not enumerate active HYPs: {e}")
        active_ids = set()

    if active_ids:
        verdict_ids = {
            v.get("hyp_id") for v in verdicts
            if isinstance(v, dict) and v.get("hyp_id")
        }
        missing = active_ids - verdict_ids
        if missing:
            issues.append(
                "missing verdicts for active HYPs: "
                + ", ".join(sorted(missing))
            )

    if runner_error:
        # File otherwise valid but runner raised mid-flight — surface so the
        # inbox shows the warning.
        issues.append(f"runner reported: {runner_error[:200]}")
    return issues


def _extract_frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    rest = text[len("---\n"):]
    end = rest.find("\n---\n")
    if end == -1:
        if rest.endswith("\n---"):
            return rest[:-len("\n---")]
        raise ValueError("malformed frontmatter: missing closing ---")
    return rest[:end]


def _load_active_hyp_ids(project_dir: Path) -> set[str]:
    store = HypothesisStore(project_dir / ".agent" / "hypotheses")
    return {h.id for h in store.list_active()}


# ---- meta-proposal filing ----


def _file_meta_proposal(
    project_dir: Path, session_id: str, issues: list[str],
) -> None:
    proposals_dir = project_dir / ".agent" / "backlog" / "proposals"
    store = ProposalStore(proposals_dir)

    # De-dup: skip if a process/session-reviewer proposal already exists for
    # this failed_session_id.
    for existing in store.filter(category="process", target="session-reviewer"):
        if existing.failed_session_id == session_id:
            print(
                f"[harness] meta-proposal already filed for {session_id}: "
                f"{existing.id}",
                file=sys.stderr,
            )
            return

    proposals_dir.mkdir(parents=True, exist_ok=True)
    prop_id = next_proposal_id(proposals_dir)
    description = (
        "Harness check detected incomplete session-reviewer output for "
        f"{session_id}. Issues: " + "; ".join(issues)
    )[:1000]
    title = f"session-reviewer incomplete: {session_id}"[:200]
    proposal = Proposal(
        id=prop_id,
        created=datetime.now(tz=timezone.utc),
        title=title,
        category="process",
        target="session-reviewer",
        description=description,
        rationale=(
            "Runtime safety net: harness check detected incomplete review "
            "output. Re-run with `ai-hats reflect session --session "
            f"{session_id}` after addressing the cause."
        ),
        failed_session_id=session_id,
    )
    try:
        path = store.create(proposal)
        print(f"[harness] filed meta-proposal: {path}", file=sys.stderr)
    except (FileExistsError, OSError, ValidationError) as e:
        print(
            f"[harness] failed to file meta-proposal for {session_id}: {e}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    sys.exit(main())
