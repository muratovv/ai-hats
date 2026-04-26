"""`ai-hats config` — view and update project configuration."""

from __future__ import annotations

import click

from ._helpers import _project_dir, console


@click.group()
def config():
    """View and update project configuration."""


@config.group(name="feedback")
def config_feedback():
    """Configure feedback loop (session-retro, judge)."""


@config_feedback.command("show")
def config_feedback_show():
    """Display current feedback configuration."""
    from ..models import ProjectConfig

    cfg = ProjectConfig.from_yaml(_project_dir() / "ai-hats.yaml")
    fc = cfg.feedback
    sr = fc.session_retro

    console.print("[bold]Feedback config[/]")
    console.print(f"  session_retro.policy:               {sr.policy.value}")
    console.print(f"  session_retro.threshold:            turns={sr.smart_threshold.min_turns}, tool_calls={sr.smart_threshold.min_tool_calls}")
    console.print(f"  session_retro.mode:                 {sr.mode}")
    console.print(f"  session_retro.background:           {sr.background}")
    console.print(f"  session_retro.reminder.enabled:     {sr.reminder.enabled}")
    console.print(f"  session_retro.reminder.max_skipped: {sr.reminder.max_skipped}")
    console.print(f"  session_retro.reminder.window_days: {sr.reminder.window_days}")
    console.print(f"  judge.policy:                       {fc.judge.policy.value}")


@config_feedback.command("session-retro")
@click.argument("policy", required=False, type=click.Choice(["off", "always", "smart", "hint"]))
@click.option("--threshold", help="Smart threshold (turns=N,tool_calls=N)")
@click.option("--mode", "retro_mode", type=click.Choice(["programmatic", "hybrid", "llm"]), help="Retro generation mode")
@click.option("--background/--no-background", default=None, help="Run retro in background")
@click.option("--reminder/--no-reminder", "reminder_enabled", default=None,
              help="Enable/disable stale-retro reminder")
@click.option("--reminder-max-skipped", "reminder_max_skipped", type=int, default=None,
              help="Trigger reminder when N skipped sessions accumulate in window")
@click.option("--reminder-window-days", "reminder_window_days", type=int, default=None,
              help="Lookback window for skipped-session count (days)")
def config_feedback_session_retro(
    policy: str | None,
    threshold: str | None,
    retro_mode: str | None,
    background: bool | None,
    reminder_enabled: bool | None,
    reminder_max_skipped: int | None,
    reminder_window_days: int | None,
):
    """Configure session-retro policy and options."""
    from ..models import FeedbackPolicy, ProjectConfig

    path = _project_dir() / "ai-hats.yaml"
    cfg = ProjectConfig.from_yaml(path)
    sr = cfg.feedback.session_retro

    nothing_to_do = (
        policy is None and threshold is None and retro_mode is None
        and background is None and reminder_enabled is None
        and reminder_max_skipped is None and reminder_window_days is None
    )
    if nothing_to_do:
        console.print(
            "[red]Specify a policy and/or options "
            "(--threshold, --mode, --background, --reminder, "
            "--reminder-max-skipped, --reminder-window-days)[/]"
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
    if retro_mode:
        sr.mode = retro_mode
    if background is not None:
        sr.background = background
    if reminder_enabled is not None:
        sr.reminder.enabled = reminder_enabled
    if reminder_max_skipped is not None:
        sr.reminder.max_skipped = reminder_max_skipped
    if reminder_window_days is not None:
        sr.reminder.window_days = reminder_window_days

    cfg.save(path)
    console.print("[green]Updated[/] feedback.session_retro")
    config_feedback_show.invoke(click.Context(config_feedback_show))


@config_feedback.command("judge")
@click.argument("policy", type=click.Choice(["off", "manual"]))
def config_feedback_judge(policy: str):
    """Configure judge policy."""
    from ..models import JudgePolicy, ProjectConfig

    path = _project_dir() / "ai-hats.yaml"
    cfg = ProjectConfig.from_yaml(path)
    cfg.feedback.judge.policy = JudgePolicy(policy)
    cfg.save(path)
    console.print(f"[green]Updated[/] feedback.judge.policy = {policy}")
