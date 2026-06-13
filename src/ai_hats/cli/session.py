"""`ai-hats session` — observability over recorded sessions.

Subcommands: list / show / audit / retro / retro-validate.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from ._helpers import _project_dir, console, exec_claude_with_retro


@click.group()
def session():
    """Browse and inspect sessions."""


# ---- session audit ----


@session.command("audit")
@click.argument("session_id", required=False)
def session_audit(session_id: str | None):
    """Show the audit log for a session (defaults to the most recent)."""
    from ..observe import SessionManager

    mgr = SessionManager(_project_dir())

    if session_id:
        s = mgr.get_session(session_id)
    else:
        sessions = mgr.list_sessions(last_n=1)
        s = sessions[0] if sessions else None

    if s is None:
        console.print("[yellow]No session found[/]")
        return

    if s.audit_path.exists():
        console.print(s.audit_path.read_text())
    else:
        console.print(f"[yellow]No audit for session {s.session_id}[/]")


# ---- session retro ----


@session.command("retro")
@click.argument("session_id", required=False)
@click.option("--last", "use_last", is_flag=True, help="Use the most recent session")
@click.option(
    "--max-retries", type=int, default=1, show_default=True,
    help="LLM retries on validation failure",
)
@click.option("--interactive", is_flag=True,
              help="After generating, hand off to a live `claude` session preloaded with the retro file")
def session_retro(
    session_id: str | None,
    use_last: bool,
    max_retries: int,
    interactive: bool,
):
    """Generate a structured session review (hats-session-review/v1, single LLM call)."""
    from ..observe import SessionManager
    from ..retro.session_review_runner import SessionReviewError, SessionReviewRunner

    project_dir = _project_dir()

    if use_last or not session_id:
        sessions = SessionManager(project_dir).list_sessions(last_n=1)
        if not sessions:
            console.print("[red]No sessions found[/]")
            sys.exit(1)
        session_id = sessions[0].session_id

    runner = SessionReviewRunner(project_dir)

    try:
        with console.status(
            f"[cyan]Generating session review for {session_id} (single LLM call)...[/]",
            spinner="dots",
        ):
            path = runner.run(session_id, max_retries=max_retries)
    except FileNotFoundError as exc:
        console.print(f"[red]Error[/]: {exc}")
        sys.exit(1)
    except SessionReviewError as exc:
        console.print(f"[red]session-reviewer failed[/]: {exc}")
        sys.exit(1)
    console.print(f"[green]Session review[/]: {path}")

    if interactive:
        exec_claude_with_retro(path, kind="session")


# ---- session retro-validate ----


@session.command("retro-validate")
@click.argument(
    "paths", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
def session_retro_validate(paths: tuple[Path, ...]) -> None:
    """Validate one or more retro files (session-retro / reflect-session)."""
    import yaml
    from pydantic import ValidationError

    from ..retro.loader import load

    failures = 0
    for path in paths:
        try:
            model, _ = load(path)
            family = type(model).__name__
            console.print(f"[green]OK[/] {path} [dim]({family})[/]")
        except (ValueError, yaml.YAMLError, OSError, ValidationError) as exc:
            failures += 1
            console.print(f"[red]FAIL[/] {path}")
            console.print(f"  [dim]{type(exc).__name__}: {exc}[/]")

    if failures:
        console.print(f"\n[red]{failures}/{len(paths)} files failed validation[/]")
        sys.exit(1)


@session.command("list")
@click.option("--last", "last_n", default=20, type=int, help="Show last N sessions (default 20)")
@click.option("--all", "show_all", is_flag=True, help="Show all sessions")
@click.option("--min-turns", default=0, type=int, help="Only sessions with >= N turns")
@click.option("--productive", is_flag=True, help="Only productive sessions (turns>0, tools>0)")
@click.option(
    "--tag", "tag_filters_raw", multiple=True,
    help="Filter by tag k=v (repeatable, AND-combined).",
)
@click.option(
    "--role", "role_filter", default=None,
    help="Filter by role (exact match against metrics.role).",
)
@click.option(
    "--since", "since_date", default=None,
    help="Filter by date YYYY-MM-DD — session on or after the given day.",
)
@click.option(
    "--json", "as_json", is_flag=True,
    help="Machine-readable JSON list of session dicts on stdout. "
         "Pipe to jq/parallel; filter values come from metrics.json.",
)
def session_list(
    last_n: int, show_all: bool, min_turns: int, productive: bool,
    tag_filters_raw: tuple[str, ...], role_filter: str | None,
    since_date: str | None, as_json: bool,
):
    """List sessions with key metrics."""
    import json

    from ..observe import SessionManager
    from ..tags import TagValidationError, parse_tag_filters

    try:
        tag_filters = parse_tag_filters(tag_filters_raw)
    except TagValidationError as e:
        raise click.BadParameter(str(e), param_hint="--tag") from e

    mgr = SessionManager(_project_dir())
    sessions = mgr.list_sessions(
        productive_only=productive,
        role_eq=role_filter,
        tag_filters=tag_filters or None,
        since_date=since_date,
    )

    if min_turns > 0:
        filtered = []
        for s in sessions:
            if s.metrics_path.exists():
                try:
                    m = json.loads(s.metrics_path.read_text())
                    if m.get("turns", 0) >= min_turns:
                        filtered.append(s)
                except (json.JSONDecodeError, OSError):
                    pass
        sessions = filtered

    if not show_all:
        sessions = sessions[-last_n:]

    if as_json:
        _emit_sessions_json(sessions)
        return

    if not sessions:
        console.print("[yellow]No sessions found[/]")
        return

    from rich.table import Table

    table = Table(show_header=True, header_style="bold")
    table.add_column("Date", style="dim")
    table.add_column("Session ID", style="cyan")
    table.add_column("Role")
    table.add_column("Provider")
    table.add_column("Turns", justify="right")
    table.add_column("Tools", justify="right")
    table.add_column("Duration", justify="right")
    table.add_column("Tokens out", justify="right")

    def _session_date(sid: str) -> str:
        try:
            return f"{sid[:4]}-{sid[4:6]}-{sid[6:8]}"
        except (IndexError, ValueError):
            return "?"

    for s in sessions:
        date_str = _session_date(s.session_id)
        if not s.metrics_path.exists():
            table.add_row(date_str, s.session_id, "?", "?", "?", "?", "?", "?")
            continue
        try:
            m = json.loads(s.metrics_path.read_text())
        except (json.JSONDecodeError, OSError):
            table.add_row(date_str, s.session_id, "?", "?", "?", "?", "?", "?")
            continue

        role = m.get("role", "?")
        provider = m.get("provider", "?")
        turns = m.get("turns", "?")
        tools = m.get("tool_calls", "?")
        tokens = m.get("tokens", {})
        tok_out = tokens.get("output", "?")
        duration = m.get("duration_wall_minutes")
        if duration is None:
            # Try to parse from audit header
            dur_str = "?"
            if s.audit_path.exists():
                header = s.audit_path.read_text()[:500]
                import re
                dur_m = re.search(r"Duration: (\d+m \d+s)", header)
                if dur_m:
                    dur_str = dur_m.group(1)
            duration = dur_str
        else:
            duration = f"{int(duration)}m"

        tok_out_str = f"{tok_out:,}" if isinstance(tok_out, int) else str(tok_out)

        table.add_row(
            date_str, s.session_id, str(role), str(provider),
            str(turns), str(tools), str(duration), tok_out_str,
        )

    console.print(table)
    console.print(f"[dim]{len(sessions)} sessions shown[/]")


def _emit_sessions_json(sessions) -> None:
    """Print a JSON list of session dicts to stdout.

    Shape per item: all metrics.json fields (role, provider, exit_code, turns,
    tool_calls, tokens, tags, ...) plus computed: session_id, session_dir,
    started_at (ISO-8601 parsed from session_id prefix if shaped that way).
    Missing metrics → only computed fields are present. Consumers pick what
    they need — shape stays forward-compatible as metrics.json gains fields.
    """
    import json

    def _started_at(sid: str) -> str | None:
        # session_id convention: YYYYMMDDTHHMMSSZ_<suffix>
        if len(sid) < 16 or sid[8] != "T" or sid[15] != "Z":
            return None
        return f"{sid[:4]}-{sid[4:6]}-{sid[6:8]}T{sid[9:11]}:{sid[11:13]}:{sid[13:15]}Z"

    out: list[dict] = []
    for s in sessions:
        item: dict = {
            "session_id": s.session_id,
            "session_dir": str(s.session_dir),
        }
        started = _started_at(s.session_id)
        if started:
            item["started_at"] = started
        if s.metrics_path.exists():
            try:
                m = json.loads(s.metrics_path.read_text())
                # Merge metrics first, then re-stamp computed fields so the
                # session_id/session_dir from metrics (if any) never shadow
                # the authoritative on-disk identity.
                merged = {**m, **item}
                item = merged
            except (json.JSONDecodeError, OSError):
                pass
        out.append(item)
    click.echo(json.dumps(out, indent=2, sort_keys=True))


def _render_usage(session) -> None:
    """Render a compact Usage section from the session's ``usage.json``.

    HATS-734 consumer for the HATS-664 producer (``compute_usage`` →
    ``usage.json``). Before this, ``usage.json`` had zero in-src readers, so a
    producer regression (the resume-mode discovery bug HATS-734 itself fixes)
    was invisible. This is the human-facing reader that makes the channel
    falsifiable.

    Fail-soft: a missing or unreadable usage.json prints nothing (no section,
    no crash) — the file is best-effort and absent for crashed / pre-664
    sessions. Only fields actually present are shown, so the block carries no
    ``None``/``?`` noise. Dynamic, transcript-derived values (skill / agent
    names, parser flags) are printed with markup disabled so a stray ``[`` in
    the data can never be mis-parsed as rich markup.
    """
    import json

    if not session.usage_path.exists():
        return
    try:
        u = json.loads(session.usage_path.read_text())
    except (json.JSONDecodeError, OSError):
        return

    lines: list[str] = []

    ao = u.get("always_on") or {}
    measured = ao.get("first_cache_creation_input_tokens")
    if isinstance(measured, int) and measured > 0:
        lines.append(f"  always_on (measured): {measured:,} tok")
    static = ao.get("static") or {}
    static_total = static.get("total_tokens")
    if isinstance(static_total, int):
        role = static.get("role")
        suffix = f" ({role})" if role else ""
        lines.append(f"  always_on (static): {static_total:,} tok{suffix}")

    agg = u.get("aggregates") or {}
    skills = agg.get("skill_loads") or {}
    if skills:
        rendered = ", ".join(f"{k} x{v}" for k, v in skills.items())
        lines.append(f"  skill_loads: {rendered}")
    calls = agg.get("tool_calls") or 0
    if calls:
        errors = agg.get("tool_errors") or 0
        rate = agg.get("tool_success_rate")
        rate_str = str(rate) if rate is not None else "n/a"
        lines.append(f"  tools: {calls} calls, {errors} err, success_rate {rate_str}")

    sidechain = u.get("sidechain") or {}
    if sidechain.get("is_sidechain"):
        lines.append(f"  sidechain: {sidechain.get('agent_name') or '?'}")

    flags = u.get("flags") or []
    if flags:
        lines.append(f"  flags: {flags}")

    if not lines:
        # usage.json present but nothing measured — still visible in Artifacts.
        return

    schema = u.get("schema_version", "usage/v1")
    console.print(f"\n[bold]Usage[/] ([dim]{schema}[/]):")
    for line in lines:
        console.print(line, markup=False)


@session.command("show")
@click.argument("session_id")
def session_show(session_id: str):
    """Show detailed metrics for a session."""
    import json

    from ..observe import SessionManager

    mgr = SessionManager(_project_dir())
    s = mgr.get_session(session_id)
    if s is None:
        console.print(f"[red]Session {session_id} not found[/]")
        sys.exit(1)

    console.print(f"[bold]Session:[/] {s.session_id}")
    console.print(f"[bold]Path:[/] {s.session_dir}")

    if s.metrics_path.exists():
        try:
            m = json.loads(s.metrics_path.read_text())
            console.print("\n[bold]Metrics:[/]")
            for k, v in m.items():
                if isinstance(v, dict):
                    console.print(f"  {k}:")
                    for k2, v2 in v.items():
                        console.print(f"    {k2}: {v2}")
                else:
                    console.print(f"  {k}: {v}")
        except (json.JSONDecodeError, OSError) as e:
            console.print(f"[yellow]Cannot read metrics: {e}[/]")

    _render_usage(s)

    artifacts = []
    for name in ("audit.md", "metrics.json", "usage.json", "trace.log", "transcript.txt", "reasoning.log", "meta_prompt.txt"):
        p = s.session_dir / name
        if p.exists() and p.stat().st_size > 0:
            artifacts.append(f"{name} ({p.stat().st_size:,}b)")
    if artifacts:
        console.print(f"\n[bold]Artifacts:[/] {', '.join(artifacts)}")
