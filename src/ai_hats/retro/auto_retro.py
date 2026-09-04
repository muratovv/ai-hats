"""Auto session-retro: policy-aware decision + execution.

Called from the session_end shell hook via:
    python3 -m ai_hats.retro.auto_retro

Reads FeedbackConfig from ai-hats.yaml and metrics from the session
directory to decide whether to generate a retro automatically.

Every decision and execution step appends one tab-separated line to
`.gitlog/session_<id>/retro.log` so skip/hint/run outcomes are
diagnosable post-hoc. Runtime also writes a `runtime decision` line
before hooks fire — so even when the hook never runs (harness crash,
SIGKILL), there is still a persistent trace.
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..models import FeedbackPolicy, ProjectConfig
from ai_hats_observe.artifacts import METRICS_JSON, RETRO_LOG, is_measured, session_dirname
from ..paths import PROJECT_CONFIG
from ..constants import ENV_SKIP_RETRO
from ..session_identity import SessionIdentity, SessionIdentityError

logger = logging.getLogger(__name__)


#: Sessions of this role ARE the auditor's output; auto-reviewing one makes the
#: reviewer its own subject (HATS-1483).
REVIEWER_ROLE = "session-reviewer"


def _read_metrics(metrics_path: Path) -> tuple[dict | None, str]:
    """Load metrics.json; ``(None, why)`` when absent or unreadable."""
    if not metrics_path.exists():
        return None, "metrics.json not found"
    try:
        with open(metrics_path) as f:
            return json.load(f), ""
    except (json.JSONDecodeError, OSError):
        return None, "metrics.json unreadable"


def should_run(
    config_path: Path,
    metrics_path: Path,
) -> tuple[str, str]:
    """Decide whether to generate a session retro.

    Returns (action, reason) where action is 'run', 'hint', or 'skip'.
    """
    config = ProjectConfig.from_yaml(config_path)
    sr = config.feedback.session_retro
    policy = sr.policy

    if policy == FeedbackPolicy.OFF:
        return "skip", "policy=off"

    metrics, metrics_issue = _read_metrics(metrics_path)

    # HATS-1483: recursion guard as a property of the SESSION — the env guard
    # (HATS-252/1402/1481) protects a process and leaked at every new entry point.
    if metrics is not None and metrics.get("role") == REVIEWER_ROLE:
        return "skip", f"role={REVIEWER_ROLE} (the auditor's own session)"

    if policy == FeedbackPolicy.ALWAYS:
        return "run", "policy=always"

    # smart / hint: check threshold
    if metrics is None:
        return "skip", metrics_issue

    # HATS-1374: the fabricated zeros used to read as a measured miss, so
    # retro.log claimed "turns=0<5" about a session nobody measured.
    if not is_measured(metrics):
        return "skip", "unmeasured (no structured transcript — threshold unevaluable)"

    turns = metrics.get("turns", 0)
    tool_calls = metrics.get("tool_calls", 0)
    threshold = sr.smart_threshold

    meets = turns >= threshold.min_turns or tool_calls >= threshold.min_tool_calls
    if not meets:
        return (
            "skip",
            f"below threshold (turns={turns}<{threshold.min_turns}, "
            f"tool_calls={tool_calls}<{threshold.min_tool_calls})",
        )

    if policy == FeedbackPolicy.HINT:
        return "hint", f"threshold met (turns={turns}, tool_calls={tool_calls})"

    return "run", f"threshold met (turns={turns}, tool_calls={tool_calls})"


def make_decision(
    layout: ProjectLayout,
    session_id: str,
) -> dict:
    """Run policy decision and return a dict rich enough to drive UI + log.

    Never raises — the failure is captured into action="skip" so the caller can
    surface "skipped (internal error: ...)" without crashing. HATS-1426: the
    promise excluded ``KeyboardInterrupt`` and, worse, the path resolution
    below sat outside the guard — the two lines the incident died on.
    """
    project_dir = layout.root
    config_path = project_dir / PROJECT_CONFIG
    try:
        metrics_path = layout.sessions.runs / session_dirname(session_id) / METRICS_JSON
        action, reason = should_run(config_path, metrics_path)
        config = ProjectConfig.from_yaml(config_path)
        sr = config.feedback.session_retro
        background = sr.background
    except (Exception, KeyboardInterrupt) as exc:
        return {
            "action": "skip",
            "reason": f"internal error: {exc!r}",
            "background": None,
            "retro_path": None,
            "log_path": _safe_log_path(layout, session_id),
            "wrap_up": None,
            "reminder": None,
        }

    retro_path = layout.sessions.retros / "sessions" / f"{session_id}.md"

    # Wrap-up nudge (HATS-214) — pure side-effect-free; any error collapses to None.
    wrap_up_info = None
    try:
        from . import reminder as reminder_mod

        wrap_up_info = reminder_mod.evaluate_wrap_up(layout, session_id)
    except Exception:  # silent-ok: the nudge is side-effect-free; any error collapses to None
        wrap_up_info = None

    reminder_info = None
    if action == "hint":
        reminder_info = {
            "count": 1,
            "command": "ai-hats reflect hypothesis",
        }

    return {
        "action": action,
        "reason": reason,
        "background": background,
        "retro_path": str(retro_path),
        "log_path": str(_retro_log_path(layout, session_id)),
        "wrap_up": wrap_up_info,
        "reminder": reminder_info,
    }


def describe_decision(decision: dict) -> str:
    """Human-readable one-liner for the session-end banner.

    Example outputs:
      "generating (bg) → <ai_hats_dir>/sessions/retros/sessions/<id>.md"
      "skipped (below threshold: turns=0<1, tool_calls=0<1)"
      "hint — ai-hats session retro <id>  (threshold met: ...)"
    """
    action = decision.get("action", "skip")
    reason = decision.get("reason", "")
    background = decision.get("background")
    retro_path = decision.get("retro_path")

    if action == "run":
        bg = "bg" if background else "fg"
        if retro_path:
            return f"generating ({bg}) → {retro_path}"
        return f"generating ({bg})"
    if action == "hint":
        # Reason contains the threshold detail; prefix with CLI call so the
        # user can copy-paste to trigger it manually.
        sid = Path(retro_path).stem if retro_path else ""
        return f"hint — ai-hats session retro {sid}  ({_parens_safe(reason)})"
    # skip
    return f"skipped ({_parens_safe(reason)})"


def _parens_safe(reason: str) -> str:
    """Strip outer redundant parens from a reason string for cleaner banner output.

    `should_run` returns things like `below threshold (turns=0<5, ...)` which
    would render as `skipped (below threshold (turns=0<5, ...))` — noisy. We
    drop one level of parenthesization when it's at the end.
    """
    s = reason.strip()
    if s.endswith(")") and "(" in s:
        head, _, tail = s.partition("(")
        # Only collapse if head is non-trivial and tail is single-paren nest.
        if tail.count("(") == 0 and tail.endswith(")"):
            return f"{head.strip()}: {tail[:-1].strip()}"
    return s


def _retro_log_path(layout: ProjectLayout, session_id: str) -> Path:
    return layout.sessions.runs / session_dirname(session_id) / RETRO_LOG


def _safe_log_path(layout: ProjectLayout, session_id: str) -> str | None:
    """Resolving this path reads ai-hats.yaml — the very thing that may be broken."""
    try:
        return str(_retro_log_path(layout, session_id))
    except (Exception, KeyboardInterrupt) as exc:
        logger.warning("retro log path unresolvable: %r", exc)
        return None


def write_retro_log(
    layout: ProjectLayout,
    session_id: str,
    source: str,
    action: str,
    detail: str,
) -> None:
    """Append one tab-separated line to `.gitlog/session_<id>/retro.log`.

    Format: `<ISO-8601 UTC>\\t<source>\\t<action>\\t<detail>\\n`
    Creates the session dir and file if they don't yet exist. Swallows
    I/O errors — observability must never break the caller.
    """
    try:
        log_path = _retro_log_path(layout, session_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Strip tabs/newlines from detail so the line stays one-per-event.
        safe_detail = detail.replace("\t", " ").replace("\n", " ")
        with open(log_path, "a") as f:
            f.write(f"{ts}\t{source}\t{action}\t{safe_detail}\n")
    except OSError:
        pass


def main(layout: ProjectLayout | None = None) -> None:
    """Entrypoint for the shell hook; the project defaults to the caller's cwd.

    Recursion guard (HATS-252): when ``HATS_SKIP_RETRO=1`` is set in the env we
    are running inside the session-reviewer's own sub-Claude process. Returning
    early breaks the otherwise unbounded spawn loop.
    """
    try:
        identity = SessionIdentity.from_env()
    except SessionIdentityError as exc:
        # HATS-1613: soft, because a session end must not raise at the shell hook
        # — but named, because the id this would log under cannot be vouched for.
        logger.warning("session retro skipped — %s", exc)
        return
    if identity is None:
        return
    session_id = identity.id

    if layout is None:
        # Boundary adapter: the shell hook's own process deserializes the
        # session's project (R6) exactly like its sibling entry points.
        from ..cli._entry import resolve_project

        layout = resolve_project().layout
    project_dir = layout.root

    if os.environ.get(ENV_SKIP_RETRO) == "1":
        write_retro_log(
            layout,
            session_id,
            "auto_retro",
            "skip",
            "recursion-guard",
        )
        return

    config_path = project_dir / PROJECT_CONFIG
    metrics_path = layout.sessions.runs / session_dirname(session_id) / METRICS_JSON

    action, reason = should_run(config_path, metrics_path)

    if action == "skip":
        write_retro_log(layout, session_id, "hook", "skip", reason)
    elif action == "hint":
        write_retro_log(layout, session_id, "hook", "hint", reason)
    else:
        # action == "run"
        config = ProjectConfig.from_yaml(config_path)
        sr = config.feedback.session_retro
        if sr.background:
            _run_background(layout, session_id)
        else:
            _run_foreground(layout, session_id)


def _run_foreground(layout: ProjectLayout, session_id: str) -> None:
    """Detach the single session-reviewer sub-process.

    Replaces the prior two-step flow (SessionRetroBuilder → reflect-session) —
    pure-Python facts + one LLM call now happen inside the reviewer runner.
    """
    _spawn_session_reviewer_background(layout, session_id)


def _spawn_session_reviewer_background(
    layout: ProjectLayout,
    session_id: str,
) -> None:
    """Detach session-reviewer sub-process; never blocks caller.

    Sets ``HATS_SKIP_RETRO=1`` in the child env so the sub-Claude session
    spawned inside the runner does not re-trigger this hook
    (:class:`SubAgentRunner` inherits ``os.environ``).

    Failures here are observability-only — never propagate.
    """
    import subprocess as sp

    log_path = _retro_log_path(layout, session_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, ENV_SKIP_RETRO: "1"}
    try:
        with open(log_path, "a") as f:
            proc = sp.Popen(
                [
                    sys.executable,
                    "-m",
                    "ai_hats.cli.reflect_session_main",
                    session_id,
                    "1",
                ],
                cwd=str(layout.root),
                stdout=f,
                stderr=f,
                start_new_session=True,
                env=env,
            )
        write_retro_log(
            layout,
            session_id,
            "session-reviewer",
            "spawn",
            f"pid={proc.pid} bg",
        )
    except Exception as exc:
        write_retro_log(
            layout,
            session_id,
            "session-reviewer",
            "spawn-failed",
            repr(exc),
        )


def _run_background(layout: ProjectLayout, session_id: str) -> None:
    import subprocess as sp

    log_path = _retro_log_path(layout, session_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Append mode so runtime "decision" line written earlier is preserved.
    with open(log_path, "a") as f:
        proc = sp.Popen(
            [
                sys.executable,
                "-m",
                "ai_hats.retro.auto_retro",
                "--foreground",
                session_id,
            ],
            cwd=str(layout.root),
            stdout=f,
            stderr=f,
            start_new_session=True,
        )
    write_retro_log(
        layout,
        session_id,
        "hook",
        "spawn",
        f"pid={proc.pid} bg",
    )


if __name__ == "__main__":
    # --foreground <session_id>: called by background Popen, runs in-process
    if len(sys.argv) == 3 and sys.argv[1] == "--foreground":
        sid = sys.argv[2]
        # Boundary adapter: the detached child deserializes the same project
        # its parent pinned into the env (R6).
        from ..cli._entry import resolve_project

        _run_foreground(resolve_project().layout, sid)
    else:
        main()
