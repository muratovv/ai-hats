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
        mode = sr.mode
        background = sr.background
    except Exception as exc:
        return {
            "action": "skip",
            "reason": f"internal error: {exc!r}",
            "mode": None,
            "background": None,
            "retro_path": None,
            "log_path": str(_retro_log_path(project_dir, session_id)),
            "reminder": None,
        }

    retro_path = (
        project_dir
        / ".agent"
        / "retrospectives"
        / "sessions"
        / mode
        / f"{session_id}.md"
    )

    # Evaluate stale-retro reminder so the runtime banner can surface it.
    # Pure side-effect-free: any error collapses to None.
    reminder_info = None
    try:
        from . import reminder as reminder_mod

        reminder_info, _ = reminder_mod.evaluate(project_dir, sr)
    except Exception:
        reminder_info = None

    return {
        "action": action,
        "reason": reason,
        "mode": mode,
        "background": background,
        "retro_path": str(retro_path),
        "log_path": str(_retro_log_path(project_dir, session_id)),
        "reminder": reminder_info,
    }


def describe_decision(decision: dict) -> str:
    """Human-readable one-liner for the session-end banner.

    Example outputs:
      "generating (llm, bg) → .agent/retrospectives/sessions/llm/<id>.md"
      "skipped (below threshold: turns=0<1, tool_calls=0<1)"
      "hint — ai-hats retro <id>  (threshold met: ...)"
    """
    action = decision.get("action", "skip")
    reason = decision.get("reason", "")
    mode = decision.get("mode") or "?"
    background = decision.get("background")
    retro_path = decision.get("retro_path")

    if action == "run":
        bg = "bg" if background else "fg"
        if retro_path:
            return f"generating ({mode}, {bg}) → {retro_path}"
        return f"generating ({mode}, {bg})"
    if action == "hint":
        # Reason contains the threshold detail; prefix with CLI call so the
        # user can copy-paste to trigger it manually.
        sid = Path(retro_path).stem if retro_path else ""
        return f"hint — ai-hats retro {sid}  ({_parens_safe(reason)})"
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
    """Entrypoint for the shell hook."""
    session_id = os.environ.get("AI_HATS_SESSION_ID", "")
    if not session_id:
        return

    project_dir = Path.cwd()
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
            _run_background(project_dir, session_id, sr.mode)
        else:
            _run_foreground(project_dir, session_id, sr.mode)

    _maybe_print_reminder(project_dir, session_id, config_path)


def _maybe_print_reminder(project_dir: Path, session_id: str, config_path: Path) -> None:
    """Evaluate stale-retro reminder and write the outcome to retro.log.

    The user-facing reminder is rendered by the runtime banner (runtime.py
    `_print_session_end`) from the dict returned by `make_decision`. This
    hook path only persists the audit line so retro.log keeps a record even
    when no banner runs (e.g. ad-hoc invocation of the hook).
    """
    from . import reminder
    try:
        sr = ProjectConfig.from_yaml(config_path).feedback.session_retro
    except Exception:
        # Observability must never break the caller — suppress any config
        # parsing failure (FileNotFoundError, YAMLError, ValidationError).
        return
    info, log_reason = reminder.evaluate(project_dir, sr)
    write_retro_log(project_dir, session_id, "reminder",
                    "fired" if info else "skipped", log_reason)


def _run_foreground(project_dir: Path, session_id: str, mode: str) -> None:
    from .builder import BuilderMode, SessionRetroBuilder
    from .llm_caller import SubprocessLLMCaller

    builder_mode = BuilderMode(mode)
    llm_caller = SubprocessLLMCaller(project_dir) if builder_mode == BuilderMode.LLM else None
    builder = SessionRetroBuilder(project_dir, llm_caller=llm_caller)
    write_retro_log(project_dir, session_id, "builder", "start", f"mode={mode}")
    try:
        path = builder.build_and_save(session_id, mode=builder_mode)
        write_retro_log(project_dir, session_id, "builder", "saved", str(path))
    except Exception as exc:
        write_retro_log(project_dir, session_id, "builder", "failed", repr(exc))

    # HATS-210: spawn reflect-session in background after LLM-mode builder
    if builder_mode == BuilderMode.LLM:
        _spawn_reflect_session_background(project_dir, session_id)


def _spawn_reflect_session_background(project_dir: Path, session_id: str) -> None:
    """Detach reflect-session sub-process; never blocks caller.

    Gates: only invoked when builder ran in LLM mode (caller checks).
    Failures here are observability-only — never propagate.
    """
    import subprocess as sp

    log_path = _retro_log_path(project_dir, session_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
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
            )
        write_retro_log(
            project_dir, session_id, "reflect-session", "spawn",
            f"pid={proc.pid} bg",
        )
    except Exception as exc:
        write_retro_log(
            project_dir, session_id, "reflect-session", "spawn-failed", repr(exc),
        )


def _run_background(project_dir: Path, session_id: str, mode: str) -> None:
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
        project_dir, session_id, "hook", "spawn", f"pid={proc.pid} mode={mode} bg",
    )


if __name__ == "__main__":
    # --foreground <session_id>: called by background Popen, runs in-process
    if len(sys.argv) == 3 and sys.argv[1] == "--foreground":
        sid = sys.argv[2]
        project_dir = Path.cwd()
        config = ProjectConfig.from_yaml(project_dir / "ai-hats.yaml")
        _run_foreground(project_dir, sid, config.feedback.session_retro.mode)
    else:
        main()
