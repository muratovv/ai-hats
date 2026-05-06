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

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..models import FeedbackPolicy, ProjectConfig


RETRO_LOG_FILENAME = "retro.log"


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

    if policy == FeedbackPolicy.ALWAYS:
        return "run", "policy=always"

    # smart / hint: check threshold
    if not metrics_path.exists():
        return "skip", "metrics.json not found"

    try:
        with open(metrics_path) as f:
            metrics = json.load(f)
    except (json.JSONDecodeError, OSError):
        return "skip", "metrics.json unreadable"

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
    project_dir: Path,
    session_id: str,
) -> dict:
    """Run policy decision and return a dict rich enough to drive UI + log.

    Never raises — any exception is captured into action="skip" so the
    caller can surface "skipped (internal error: ...)" without crashing.
    """
    config_path = project_dir / "ai-hats.yaml"
    metrics_path = project_dir / ".gitlog" / f"session_{session_id}" / "metrics.json"
    try:
        action, reason = should_run(config_path, metrics_path)
        config = ProjectConfig.from_yaml(config_path)
        sr = config.feedback.session_retro
        background = sr.background
    except Exception as exc:
        return {
            "action": "skip",
            "reason": f"internal error: {exc!r}",
            "background": None,
            "retro_path": None,
            "log_path": str(_retro_log_path(project_dir, session_id)),
            "wrap_up": None,
        }

    retro_path = (
        project_dir
        / ".agent"
        / "retrospectives"
        / "sessions"
        / f"{session_id}.md"
    )

    # Wrap-up nudge (HATS-214) — pure side-effect-free; any error collapses to None.
    wrap_up_info = None
    try:
        from . import reminder as reminder_mod

        wrap_up_info = reminder_mod.evaluate_wrap_up(project_dir, session_id)
    except Exception:
        wrap_up_info = None

    return {
        "action": action,
        "reason": reason,
        "background": background,
        "retro_path": str(retro_path),
        "log_path": str(_retro_log_path(project_dir, session_id)),
        "wrap_up": wrap_up_info,
    }


def describe_decision(decision: dict) -> str:
    """Human-readable one-liner for the session-end banner.

    Example outputs:
      "generating (bg) → .agent/retrospectives/sessions/<id>.md"
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


def _retro_log_path(project_dir: Path, session_id: str) -> Path:
    return project_dir / ".gitlog" / f"session_{session_id}" / RETRO_LOG_FILENAME


def write_retro_log(
    project_dir: Path,
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
        log_path = _retro_log_path(project_dir, session_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Strip tabs/newlines from detail so the line stays one-per-event.
        safe_detail = detail.replace("\t", " ").replace("\n", " ")
        with open(log_path, "a") as f:
            f.write(f"{ts}\t{source}\t{action}\t{safe_detail}\n")
    except OSError:
        pass


def main() -> None:
    """Entrypoint for the shell hook.

    Recursion guard (HATS-252): when ``HATS_SKIP_RETRO=1`` is set in the env we
    are running inside the session-reviewer's own sub-Claude process. Returning
    early breaks the otherwise unbounded spawn loop.
    """
    session_id = os.environ.get("AI_HATS_SESSION_ID", "")
    if not session_id:
        return

    project_dir = Path.cwd()

    if os.environ.get("HATS_SKIP_RETRO") == "1":
        write_retro_log(
            project_dir, session_id, "auto_retro", "skip", "recursion-guard",
        )
        return

    config_path = project_dir / "ai-hats.yaml"
    metrics_path = project_dir / ".gitlog" / f"session_{session_id}" / "metrics.json"

    action, reason = should_run(config_path, metrics_path)

    if action == "skip":
        write_retro_log(project_dir, session_id, "hook", "skip", reason)
    elif action == "hint":
        write_retro_log(project_dir, session_id, "hook", "hint", reason)
    else:
        # action == "run"
        config = ProjectConfig.from_yaml(config_path)
        sr = config.feedback.session_retro
        if sr.background:
            _run_background(project_dir, session_id)
        else:
            _run_foreground(project_dir, session_id)


def _run_foreground(project_dir: Path, session_id: str) -> None:
    """Detach the single session-reviewer sub-process.

    Replaces the prior two-step flow (SessionRetroBuilder → reflect-session) —
    pure-Python facts + one LLM call now happen inside the reviewer runner.
    """
    _spawn_session_reviewer_background(project_dir, session_id)


def _spawn_session_reviewer_background(
    project_dir: Path, session_id: str,
) -> None:
    """Detach session-reviewer sub-process; never blocks caller.

    Sets ``HATS_SKIP_RETRO=1`` in the child env so the sub-Claude session
    spawned inside the runner does not re-trigger this hook
    (:class:`SubAgentRunner` inherits ``os.environ``).

    Failures here are observability-only — never propagate.
    """
    import subprocess as sp

    log_path = _retro_log_path(project_dir, session_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "HATS_SKIP_RETRO": "1"}
    try:
        with open(log_path, "a") as f:
            proc = sp.Popen(
                [
                    sys.executable,
                    "-m", "ai_hats.cli.reflect_session_main",
                    session_id, "1",
                ],
                cwd=str(project_dir),
                stdout=f,
                stderr=f,
                start_new_session=True,
                env=env,
            )
        write_retro_log(
            project_dir, session_id, "session-reviewer", "spawn",
            f"pid={proc.pid} bg",
        )
    except Exception as exc:
        write_retro_log(
            project_dir, session_id, "session-reviewer", "spawn-failed",
            repr(exc),
        )


def _run_background(project_dir: Path, session_id: str) -> None:
    import subprocess as sp

    log_path = _retro_log_path(project_dir, session_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Append mode so runtime "decision" line written earlier is preserved.
    with open(log_path, "a") as f:
        proc = sp.Popen(
            [
                sys.executable, "-m", "ai_hats.retro.auto_retro",
                "--foreground", session_id,
            ],
            cwd=str(project_dir),
            stdout=f,
            stderr=f,
            start_new_session=True,
        )
    write_retro_log(
        project_dir, session_id, "hook", "spawn", f"pid={proc.pid} bg",
    )


if __name__ == "__main__":
    # --foreground <session_id>: called by background Popen, runs in-process
    if len(sys.argv) == 3 and sys.argv[1] == "--foreground":
        sid = sys.argv[2]
        project_dir = Path.cwd()
        _run_foreground(project_dir, sid)
    else:
        main()
