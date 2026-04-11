"""Auto session-retro: policy-aware decision + execution.

Called from the session_end shell hook via:
    python3 -m ai_hats.retro.auto_retro

Reads FeedbackConfig from profile.json and metrics from the session
directory to decide whether to generate a retro automatically.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from ..models import FeedbackPolicy, ProfileConfig


def should_run(
    profile_path: Path,
    metrics_path: Path,
) -> tuple[str, str]:
    """Decide whether to generate a session retro.

    Returns (action, reason) where action is 'run', 'hint', or 'skip'.
    """
    config = ProfileConfig.load(profile_path)
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


def main() -> None:
    """Entrypoint for the shell hook."""
    session_id = os.environ.get("AI_HATS_SESSION_ID", "")
    if not session_id:
        return

    project_dir = Path.cwd()
    profile_path = project_dir / "profile.json"
    metrics_path = project_dir / ".gitlog" / f"session_{session_id}" / "metrics.json"

    action, reason = should_run(profile_path, metrics_path)

    if action == "skip":
        print(f"[auto-retro] skip: {reason}", file=sys.stderr)
        return

    if action == "hint":
        print(f"[auto-retro] hint: ai-hats retro {session_id}  ({reason})", file=sys.stderr)
        return

    # action == "run"
    config = ProfileConfig.load(profile_path)
    mode = config.feedback.session_retro.mode

    from .builder import BuilderMode, SessionRetroBuilder
    from .llm_caller import SubprocessLLMCaller

    builder_mode = BuilderMode(mode)
    llm_caller = SubprocessLLMCaller(project_dir) if builder_mode == BuilderMode.LLM else None
    builder = SessionRetroBuilder(project_dir, llm_caller=llm_caller)
    print(f"[auto-retro] generating {mode} retro for {session_id}...", file=sys.stderr)
    try:
        path = builder.build_and_save(session_id, mode=builder_mode)
        print(f"[auto-retro] saved: {path}", file=sys.stderr)
    except Exception as exc:
        print(f"[auto-retro] failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
