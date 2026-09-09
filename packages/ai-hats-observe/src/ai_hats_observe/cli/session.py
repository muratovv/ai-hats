"""`ai-hats session` browse commands — observability over recorded sessions.

Standalone subcommands: list / show / audit. Runs under the worktree-free ``STANDALONE``
host (core-only). The integrator attaches its own ``Host`` with
AI_HATS_DIR/yaml-aware resolvers at mount and re-attaches the retro subcommands
(``retro`` / ``retro-validate``, downstream consumers that stay integrator-side).
"""

from __future__ import annotations

import json
import re
import sys

import click

from ..artifacts import (
    AUDIT_MD,
    FLAG_NO_STRUCTURED_TRANSCRIPT,
    META_PROMPT_TXT,
    METRICS_JSON,
    REASONING_LOG,
    TRACE_LOG,
    TRANSCRIPT_TXT,
    USAGE_JSON,
    is_measured,
    session_start_dt,
)
from ..session import _load_metrics_safe
from . import _host


@click.group()
def session():
    """Browse and inspect sessions."""


# ---- session audit ----


@session.command("audit")
@click.argument("session_id", required=False)
def session_audit(session_id: str | None):
    """Show the audit log for a session (defaults to the most recent)."""

    from ..session import SessionManager

    layout = _host.host().layout()
    mgr = SessionManager(layout.root, runs_dir=layout.sessions.runs)

    if session_id:
        s = mgr.get_session(session_id)
    else:
        sessions = mgr.list_sessions(last_n=1)
        s = sessions[0] if sessions else None

    if s is None:
        _host.host().console.print("[yellow]No session found[/]")
        return

    if s.audit_path.exists():
        _host.host().console.print(s.audit_path.read_text())
    else:
        _host.host().console.print(f"[yellow]No audit for session {s.session_id}[/]")


@session.command("list")
@click.option("--last", "last_n", default=20, type=int, help="Show last N sessions (default 20)")
@click.option("--all", "show_all", is_flag=True, help="Show all sessions")
@click.option("--min-turns", default=0, type=int, help="Only sessions with >= N turns")
@click.option("--productive", is_flag=True, help="Only productive sessions (turns>0, tools>0)")
@click.option(
    "--tag",
    "tag_filters_raw",
    multiple=True,
    help="Filter by tag k=v (repeatable, AND-combined).",
)
@click.option(
    "--role",
    "role_filter",
    default=None,
    help="Filter by role (exact match against metrics.role).",
)
@click.option(
    "--since",
    "since_date",
    default=None,
    help="Filter by date YYYY-MM-DD — session on or after the given day.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Machine-readable JSON list of session dicts on stdout. "
    "Pipe to jq/parallel; filter values come from metrics.json.",
)
def session_list(
    last_n: int,
    show_all: bool,
    min_turns: int,
    productive: bool,
    tag_filters_raw: tuple[str, ...],
    role_filter: str | None,
    since_date: str | None,
    as_json: bool,
):
    """List sessions with key metrics."""
    import json

    from ..session import SessionManager

    try:
        tag_filters = _host.host().tag_filter_parser(tag_filters_raw)
    except ValueError as e:
        raise click.BadParameter(str(e), param_hint="--tag") from e

    layout = _host.host().layout()
    mgr = SessionManager(layout.root, runs_dir=layout.sessions.runs)
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
        _host.host().console.print("[yellow]No sessions found[/]")
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
            date_str,
            s.session_id,
            str(role),
            str(provider),
            str(turns),
            str(tools),
            str(duration),
            tok_out_str,
        )

    _host.host().console.print(table)
    _host.host().console.print(f"[dim]{len(sessions)} sessions shown[/]")


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
        # HATS-1248: gated on a `YYYYMMDDTHHMMSSZ_` shape nothing ever minted,
        # so this field was absent from every real session.
        start = session_start_dt(sid)
        return start.strftime("%Y-%m-%dT%H:%M:%SZ") if start else None

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
    role = static.get("role")
    suffix = f" ({role})" if role else ""
    # HATS-957: prefer the honest always-on figure (injection + rule bodies +
    # skill name/description); skill BODIES load on demand and are shown apart.
    always_on_static = static.get("always_on_tokens")
    if isinstance(always_on_static, int):
        lines.append(f"  always_on (static): {always_on_static:,} tok{suffix}")
        on_demand = static.get("on_demand_tokens")
        if isinstance(on_demand, int) and on_demand > 0:
            lines.append(f"  on-demand skills (if invoked): {on_demand:,} tok")
    else:
        # Pre-HATS-957 usage.json recorded only the conflated total.
        static_total = static.get("total_tokens")
        if isinstance(static_total, int):
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
    _host.host().console.print(f"\n[bold]Usage[/] ([dim]{schema}[/]):")
    for line in lines:
        _host.host().console.print(line, markup=False)


def _render_diagnostics(session) -> None:
    """Render startup diagnostics and post-session banners from diagnostics.json."""
    import json

    diag_file = session.session_dir / "diagnostics.json"
    if not diag_file.exists():
        return
    try:
        diag = json.loads(diag_file.read_text(encoding="utf-8"))
        if not isinstance(diag, dict):
            return
    except (json.JSONDecodeError, OSError):
        return

    startup = diag.get("startup")
    if isinstance(startup, dict):
        notices = startup.get("notices") or []
        hold = startup.get("hold_seconds", 0.0)
        _host.host().console.print(f"\n[bold]Startup Diagnostics[/] ([dim]hold: {hold}s[/]):")
        if not notices:
            _host.host().console.print("  [green]✓ Clean start (no warnings or notes)[/]")
        else:
            for n in notices:
                lvl = n.get("level", "info")
                txt = n.get("text", "")
                if lvl == "note":
                    _host.host().console.print(f"  [green]✓ {txt}[/]", markup=False)
                elif lvl == "fatal":
                    _host.host().console.print(f"  [red]✕ {txt}[/]", markup=False)
                else:
                    _host.host().console.print(f"  [yellow]⚠ {txt}[/]", markup=False)

    completion = diag.get("completion")
    retro = diag.get("retro_reminder")
    update = diag.get("update_banner")

    if completion or retro or update:
        _host.host().console.print("\n[bold]Post-Session Diagnostics & Banners[/]:")

        if isinstance(completion, dict):
            dur = completion.get("duration")
            if dur is None and "duration_s" in completion:
                ds = completion.get("duration_s")
                if ds is not None:
                    dur = f"{ds:.1f}s"
            dur_str = dur if dur is not None else "?"

            turns = completion.get("req_count")
            role = completion.get("role")
            exit_code = completion.get("exit_code")
            error = completion.get("error")
            timed_out = completion.get("timed_out")

            parts = [f"duration {dur_str}"]
            if turns is not None:
                parts.append(f"{turns} turns")
            if role is not None:
                parts.append(f"role '{role}'")
            if exit_code is not None:
                parts.append(f"exit {exit_code}")
            if timed_out:
                parts.append("TIMED OUT")
            if error:
                parts.append(f"error: {error}")

            _host.host().console.print(f"  ✨ [green]Completion[/]: {', '.join(parts)}")

        if isinstance(retro, dict):
            rem = retro.get("reminder")
            if isinstance(rem, dict):
                _host.host().console.print(
                    f"  📝 [cyan]Retro Reminder[/]: Reflect through {rem.get('count')} sessions (`{rem.get('command')}`)"
                )
            wrap = retro.get("wrap_up")
            if isinstance(wrap, dict):
                _host.host().console.print(
                    f"  🧹 [cyan]Wrap Up[/]: {wrap.get('tasks_closed')} tasks closed in {wrap.get('duration_min')}m"
                )

        if isinstance(update, dict):
            inst = update.get("installed_label") or update.get("installed_sha", "?")
            latest = update.get("latest_label") or update.get("latest_sha", "?")
            behind = update.get("behind", 0)
            _host.host().console.print(
                f"  🚀 [yellow]Update Available[/]: {inst} → {latest} (+{behind} commits). Run: ai-hats self update"
            )


@session.command("show")
@click.argument("session_id")
def session_show(session_id: str):
    """Show detailed metrics for a session."""
    import json

    from ..session import SessionManager

    layout = _host.host().layout()
    mgr = SessionManager(layout.root, runs_dir=layout.sessions.runs)
    s = mgr.get_session(session_id)
    if s is None:
        _host.host().console.print(f"[red]Session {session_id} not found[/]")
        sys.exit(1)

    _host.host().console.print(f"[bold]Session:[/] {s.session_id}")
    _host.host().console.print(f"[bold]Path:[/] {s.session_dir}")

    if s.metrics_path.exists():
        try:
            m = json.loads(s.metrics_path.read_text())
            _host.host().console.print("\n[bold]Metrics:[/]")
            for k, v in m.items():
                if isinstance(v, dict):
                    _host.host().console.print(f"  {k}:")
                    for k2, v2 in v.items():
                        _host.host().console.print(f"    {k2}: {v2}")
                else:
                    _host.host().console.print(f"  {k}: {v}")
        except (json.JSONDecodeError, OSError) as e:
            _host.host().console.print(f"[yellow]Cannot read metrics: {e}[/]")

    _render_diagnostics(s)
    _render_usage(s)

    artifacts = []
    for name in (
        "diagnostics.json",
        AUDIT_MD,
        METRICS_JSON,
        USAGE_JSON,
        TRACE_LOG,
        TRANSCRIPT_TXT,
        REASONING_LOG,
        META_PROMPT_TXT,
    ):
        p = s.session_dir / name
        if p.exists() and p.stat().st_size > 0:
            artifacts.append(f"{name} ({p.stat().st_size:,}b)")
    if artifacts:
        _host.host().console.print(f"\n[bold]Artifacts:[/] {', '.join(artifacts)}")


# ---- session backfill ----

# One match, one line: `.` never crosses a newline, so provider and id cannot be
# paired across the file. The `[SYS]` prefix is anchored because trace.log is the
# whole PTY stream — `[RES]` lines are terminal output and may contain anything.
_LAUNCH_LINE = re.compile(
    r"^\d{2}:\d{2}:\d{2}\.\d{3} \[SYS\] Launching: (?P<provider>\S+)"
    r"(?:.*?--session-id[= ](?P<session_id>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}))?",
    re.MULTILINE,
)


def _identity_from_trace(s) -> tuple[str | None, str | None]:
    """Recover ``(provider, provider_session_id)`` from the logged launch line.

    Pre-HATS-1374 records persisted neither (and the 48 hardest cases have no
    metrics.json at all), but the runner logs the whole command —
    ``[SYS] Launching: claude … --session-id <uuid>`` — into trace.log.

    HATS-1397: both halves must come from the SAME launch line. Two independent
    searches over the whole stream paired this session's provider with a uuid
    printed later by the terminal, and ``_backfill_one`` persisted it forever.
    Fails closed: a ``--resume`` launch carries no flag, so the id stays ``None``.
    """
    if not s.trace_path.exists():
        return None, None
    try:
        text = s.trace_path.read_text(errors="replace")
    except OSError:
        return None, None
    launch = _LAUNCH_LINE.search(text)
    if launch is None:
        return None, None
    return launch.group("provider"), launch.group("session_id")


def _backfill_one(s, *, project_dir, dry_run: bool) -> dict:
    """Re-derive one session's counters from its transcript. Returns a row dict."""
    from ..audit import AuditWriter

    metrics = _load_metrics_safe(s) or {}
    row = {
        "session_id": s.session_id,
        "provider": metrics.get("provider") or "?",
        "before": "measured" if is_measured(metrics) else "unmeasured",
        "after": "-",
        "turns": "-",
        "tool_calls": "-",
        "note": "",
    }
    row["after"] = row["before"]

    # HATS-1397: `build` replaces audit.md wholesale, so rewriting a session that
    # is still running destroys the `## Events` log it is appending to and races
    # `finalize_audit` for metrics.json. `finalized` exists to answer this.
    if metrics.get("finalized") is False:
        row["note"] = "not finalized (session still running)"
        return row

    # Exact match only: live discovery falls back to the freshest transcript,
    # which retroactively resolves a stranger's (HATS-1374 — a dry run
    # attributed one identical turns=5/tools=216 to 60 unrelated sessions).
    provider = metrics.get("provider") or ""
    provider_session_id = metrics.get("claude_session_id") or None
    from_trace = False
    if not provider or not provider_session_id:
        trace_provider, trace_session_id = _identity_from_trace(s)
        provider = provider or (trace_provider or "")
        if not provider_session_id and trace_session_id:
            provider_session_id = trace_session_id
            from_trace = True
        row["provider"] = provider or "?"

    resolver, parser = _host.host().provider_adapter(provider)
    if resolver is None or not provider_session_id:
        row["note"] = "no provider session id"
        return row

    # HATS-1397: the resolver refuses to guess once given an id, so the stem check
    # that used to sit here is gone — it only ever described claude's filenames and
    # rejected agy (`…/<psid>/…/transcript.jsonl`) and cline (`<psid>.messages`).
    jsonl_path = resolver(project_dir, s.session_id, provider_session_id=provider_session_id)
    has_existing = (
        any(p.exists() for p in jsonl_path)
        if isinstance(jsonl_path, (list, tuple))
        else (jsonl_path is not None and jsonl_path.exists())
    )
    if not jsonl_path or not has_existing:
        row["note"] = "no transcript"
        return row

    writer = AuditWriter(parser) if parser is not None else AuditWriter()

    suffix = " (id from trace)" if from_trace else ""

    if dry_run:
        parsed = writer.parser.parse(jsonl_path, s.trace_path)
        measurable = FLAG_NO_STRUCTURED_TRANSCRIPT not in parsed.flags
        row["after"] = "measured" if measurable else "unmeasured"
        row["turns"] = str(len(parsed.turns))
        row["tool_calls"] = str(sum(len(t.tools) for t in parsed.turns))
        row["note"] = ("would rewrite" if measurable else "unparseable") + suffix
        return row

    if from_trace:
        # Persist the recovered identity so the link survives the next audit,
        # which deletes trace.log — the source we just read it from.
        metrics["claude_session_id"] = provider_session_id
        s.write_artifact_text(s.metrics_path, json.dumps(metrics, indent=2))

    # keep_raw: a backfill must not consume trace.log — it is the only source
    # left for surfaces whose transcript cannot be recovered (HATS-1374).
    writer.build(s, jsonl_path=jsonl_path, keep_raw=True)
    after = _load_metrics_safe(s) or {}
    row["after"] = "measured" if is_measured(after) else "unmeasured"
    row["turns"] = str(after.get("turns", "-"))
    row["tool_calls"] = str(after.get("tool_calls", "-"))
    row["note"] = "rewritten" + suffix
    return row


@session.command("backfill")
@click.argument("session_ids", nargs=-1)
@click.option("--last", "last_n", default=0, type=int, help="Backfill the last N sessions.")
@click.option("--all", "show_all", is_flag=True, help="Backfill every session.")
@click.option("--dry-run", is_flag=True, help="Report what would change without writing anything.")
@click.option(
    "--force",
    is_flag=True,
    help="Also re-derive sessions already marked measured (default: skip them).",
)
def session_backfill(
    session_ids: tuple[str, ...], last_n: int, show_all: bool, dry_run: bool, force: bool
):
    """Re-derive turns/tokens/tool_calls for past sessions from their transcripts.

    Metrics used to be written once at teardown, so a session whose enrichment
    failed kept its gap forever even though the transcript was still on disk.
    """
    from rich.table import Table

    from ..session import SessionManager

    layout = _host.host().layout()
    mgr = SessionManager(layout.root, runs_dir=layout.sessions.runs)

    if session_ids:
        sessions = [mgr.get_session(sid) for sid in session_ids]
        sessions = [s for s in sessions if s is not None]
    elif show_all:
        sessions = mgr.list_sessions()
    elif last_n:
        sessions = mgr.list_sessions(last_n=last_n)
    else:
        raise click.UsageError("pass session ids, or one of --last N / --all")

    if not sessions:
        _host.host().console.print("[yellow]No matching sessions[/]")
        return

    rows, skipped = [], 0
    for s in sessions:
        if not force and is_measured(_load_metrics_safe(s) or {}):
            skipped += 1
            continue
        rows.append(_backfill_one(s, project_dir=layout.root, dry_run=dry_run))

    table = Table(title="session backfill — dry run" if dry_run else "session backfill")
    table.add_column("session", no_wrap=True)
    table.add_column("provider")
    table.add_column("was")
    table.add_column("now")
    table.add_column("turns", justify="right")
    table.add_column("tools", justify="right")
    table.add_column("note")
    for r in rows:
        table.add_row(
            r["session_id"],
            r["provider"],
            r["before"],
            r["after"],
            r["turns"],
            r["tool_calls"],
            r["note"],
        )
    _host.host().console.print(table)

    recovered = sum(1 for r in rows if r["before"] == "unmeasured" and r["after"] == "measured")
    no_transcript = sum(1 for r in rows if r["note"].startswith("no "))
    _host.host().console.print(
        f"\n[bold]{len(rows)}[/] examined, [bold green]{recovered}[/] recoverable, "
        f"[bold]{no_transcript}[/] without a transcript, [bold]{skipped}[/] already measured"
        + (" [dim](nothing written — dry run)[/]" if dry_run else "")
    )
