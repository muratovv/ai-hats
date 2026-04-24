"""`ai-hats session` + `ai-hats audit` — observability over recorded sessions."""

from __future__ import annotations

import sys

import click

from ._helpers import _project_dir, console


@click.command()
@click.option("--session", default=None, help="Session ID to audit")
def audit(session: str | None):
    """Show audit for a session."""
    from ..observe import SessionManager

    mgr = SessionManager(_project_dir())

    if session:
        s = mgr.get_session(session)
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


@click.group()
def session():
    """Browse and inspect sessions."""


@session.command("list")
@click.option("--last", "last_n", default=20, type=int, help="Show last N sessions (default 20)")
@click.option("--all", "show_all", is_flag=True, help="Show all sessions")
@click.option("--min-turns", default=0, type=int, help="Only sessions with >= N turns")
@click.option("--productive", is_flag=True, help="Only productive sessions (turns>0, tools>0)")
@click.option("--unreviewed", is_flag=True, help="Only sessions not yet in any bundle")
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
    last_n: int, show_all: bool, min_turns: int, productive: bool, unreviewed: bool,
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

    if unreviewed:
        from ..retro.bundles import BundleManager
        reviewed = BundleManager(_project_dir()).reviewed_session_ids()
        sessions = [s for s in sessions if s.session_id not in reviewed]

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

    artifacts = []
    for name in ("audit.md", "metrics.json", "trace.log", "transcript.txt", "reasoning.log", "meta_prompt.txt"):
        p = s.session_dir / name
        if p.exists() and p.stat().st_size > 0:
            artifacts.append(f"{name} ({p.stat().st_size:,}b)")
    if artifacts:
        console.print(f"\n[bold]Artifacts:[/] {', '.join(artifacts)}")
