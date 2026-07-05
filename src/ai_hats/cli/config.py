"""`ai-hats config` — view and update project configuration."""

from __future__ import annotations

import click

from ..paths import PROJECT_CONFIG
from ._helpers import _project_dir, console


@click.group()
def config():
    """View and update project configuration."""


@config.group(name="feedback")
def config_feedback():
    """Configure feedback loop (session-retro)."""


@config_feedback.command("show")
def config_feedback_show():
    """Display current feedback configuration."""
    from ..models import ProjectConfig

    cfg = ProjectConfig.from_yaml(_project_dir() / PROJECT_CONFIG)
    sr = cfg.feedback.session_retro

    console.print("[bold]Feedback config[/]")
    console.print(f"  session_retro.policy:               {sr.policy.value}")
    console.print(f"  session_retro.threshold:            turns={sr.smart_threshold.min_turns}, tool_calls={sr.smart_threshold.min_tool_calls}")
    console.print(f"  session_retro.background:           {sr.background}")
    console.print(f"  session_retro.review_model:         {sr.review_model or '(provider default)'}")


@config_feedback.command("session-retro")
@click.argument("policy", required=False, type=click.Choice(["off", "always", "smart", "hint"]))
@click.option("--threshold", help="Smart threshold (turns=N,tool_calls=N)")
@click.option("--background/--no-background", default=None, help="Run retro in background")
def config_feedback_session_retro(
    policy: str | None,
    threshold: str | None,
    background: bool | None,
):
    """Configure session-retro policy and options."""
    from ..models import FeedbackPolicy, ProjectConfig

    path = _project_dir() / PROJECT_CONFIG
    cfg = ProjectConfig.from_yaml(path)
    sr = cfg.feedback.session_retro

    nothing_to_do = policy is None and threshold is None and background is None
    if nothing_to_do:
        console.print(
            "[red]Specify a policy and/or options (--threshold, --background)[/]"
        )
        raise SystemExit(1)

    if policy:
        sr.policy = FeedbackPolicy(policy)
    if threshold:
        for part in threshold.split(","):
            key, _, val = part.strip().partition("=")
            if key == "turns":
                sr.smart_threshold.min_turns = int(val)
            elif key == "tool_calls":
                sr.smart_threshold.min_tool_calls = int(val)
            else:
                console.print(f"[red]Unknown threshold key: {key}[/]")
                raise SystemExit(1)
    if background is not None:
        sr.background = background

    # Delta-write only the session_retro subtree this command owns (HATS-526).
    from ..config.project import locked_update

    locked_update(path, lambda c: setattr(c.feedback, "session_retro", sr))
    console.print("[green]Updated[/] feedback.session_retro")
    config_feedback_show.invoke(click.Context(config_feedback_show))
