"""`ai-hats task hyp` — manage hypothesis backlog (<ai_hats_dir>/tracker/hypotheses/HYP-*.yaml).

Subcommands:
  list, show           — read-only views
  create               — create a new HYP-NNN.yaml (auto-id)
  append-verdict       — atomic ValidationLogEntry append (used by reflect-session)
  set-status           — flip status (active|confirmed|refuted|stalled)
  autoclose            — safe quorum close-as-gone of refuted HYPs (HATS-769)
  migrate              — one-shot normalize all HYP files under current schema
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import click
import yaml

from ..utils.atomic_io import atomic_write_text
from ..hypothesis import (
    Hypothesis,
    HypothesisStore,
    ValidationLogEntry,
    next_hypothesis_id,
)
from ._helpers import _project_dir, console


def _hyp_dir(project_dir: Path) -> Path:
    from ..paths import hypotheses_dir

    return hypotheses_dir(project_dir)


def _store(project_dir: Path | None = None) -> HypothesisStore:
    pd = project_dir or _project_dir()
    return HypothesisStore(_hyp_dir(pd))


@click.group()
def hyp():
    """Manage hypothesis backlog."""


@hyp.command("list")
@click.option(
    "--status",
    type=click.Choice(["active", "confirmed", "refuted", "stalled"]),
    default=None,
    help="Filter by status",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON")
def hyp_list(status: str | None, as_json: bool):
    """List hypotheses (id, status, title, last_rule_revision_date)."""
    store = _store()
    items = store.list_all()
    if status:
        items = [h for h in items if h.status == status]
    if as_json:
        click.echo(
            json.dumps(
                [
                    {
                        "id": h.id,
                        "status": h.status,
                        "title": h.title,
                        "last_rule_revision_date": (
                            h.last_rule_revision_date.isoformat()
                            if h.last_rule_revision_date
                            else None
                        ),
                    }
                    for h in items
                ],
                indent=2,
            )
        )
        return
    for h in items:
        rev = h.last_rule_revision_date.isoformat() if h.last_rule_revision_date else "—"
        click.echo(f"{h.id}  [{h.status:11s}]  rev={rev}  {h.title}")


@hyp.command("show")
@click.argument("hyp_id")
def hyp_show(hyp_id: str):
    """Show one hypothesis (full content)."""
    store = _store()
    p = store.path(hyp_id)
    if not p.exists():
        raise click.ClickException(f"{hyp_id} not found at {p}")
    click.echo(p.read_text())


@hyp.command("create")
@click.option("--title", required=True, help="Short title")
@click.option("--hypothesis", "hypothesis_text", required=True, help="The hypothesis statement")
@click.option("--source-task", required=True, help="Originating task id (HATS-NNN)")
@click.option("--baseline", default=None, help="Pre-change observation (free text)")
@click.option(
    "--expected-outcome",
    "expected_outcomes",
    multiple=True,
    help="Expected outcome bullet (repeatable)",
)
@click.option("--observation-window", default=None, help="e.g., '4 sessions' or '2 weeks'")
@click.option("--success-criterion", default=None, help="How a verdict is decided")
@click.option("--rollback-condition", default=None, help="When to revert")
@click.option(
    "--verification-protocol",
    default=None,
    help="Verification protocol for library-change companion HYPs (HATS-623)",
)
@click.option("--json", "as_json", is_flag=True)
def hyp_create(
    title: str,
    hypothesis_text: str,
    source_task: str,
    baseline: str | None,
    expected_outcomes: tuple[str, ...],
    observation_window: str | None,
    success_criterion: str | None,
    rollback_condition: str | None,
    verification_protocol: str | None,
    as_json: bool,
):
    """Create a new HYP-NNN. Auto-id; status=active; created=today (UTC)."""
    store = _store()
    new_id = next_hypothesis_id(store.dir)
    h = Hypothesis(
        id=new_id,
        title=title,
        status="active",
        created=datetime.now(tz=timezone.utc).date(),
        source_task=source_task,
        hypothesis=hypothesis_text,
        baseline=baseline,
        expected_outcome=list(expected_outcomes),
        observation_window=observation_window,
        success_criterion=success_criterion,
        rollback_condition=rollback_condition,
        # Extra field (model is extra="allow"); dropped from YAML when None
        # via model_dump(exclude_none=True), same as the optionals above.
        verification_protocol=verification_protocol,
    )
    try:
        store.create(h)
    except FileExistsError as e:
        console.print(f"[red]Error[/]: {e}")
        sys.exit(1)
    if as_json:
        click.echo(json.dumps({"id": new_id}))
    else:
        console.print(f"[green]✓[/green] Created {new_id} (status=active)")


@hyp.command("set-status")
@click.option("--hyp", "hyp_id", required=True, help="Target hypothesis (HYP-NNN)")
@click.option(
    "--status",
    type=click.Choice(["active", "confirmed", "refuted", "stalled"]),
    required=True,
)
def hyp_set_status(hyp_id: str, status: str):
    """Flip hypothesis status (atomic, filelock-protected)."""
    store = _store()
    if not store.path(hyp_id).exists():
        raise click.ClickException(f"{hyp_id} not found")
    h = store.set_status(hyp_id, status)
    console.print(f"[green]✓[/green] {hyp_id}: status={h.status}")


@hyp.command("autoclose")
@click.option("--k", type=int, default=None, help="Quorum threshold (default 3)")
@click.option("--dry-run", is_flag=True, help="Print candidates; mutate nothing")
def hyp_autoclose(k: int | None, dry_run: bool):
    """Close-as-gone every active HYP with a quorum of K independent refuted verdicts.

    Safe direction only — never confirms. Each closure is logged to
    validation_log and reversed with `set-status --status active` (HATS-769).
    This is the same sweep the `finalize-hitl` pipeline runs after each session.
    """
    from ..hypothesis.quorum import DEFAULT_QUORUM_K, apply_closure, find_quorum_closures

    threshold = DEFAULT_QUORUM_K if k is None else k
    if threshold < 1:
        raise click.ClickException("--k must be >= 1")
    store = _store()
    closures = find_quorum_closures(store, threshold)
    if not closures:
        console.print("closed: none")
        return
    if dry_run:
        for c in closures:
            console.print(
                f"[yellow]would close[/yellow] {c.hyp_id} "
                f"(refuted by {len(c.refute_sessions)} independent sessions: "
                f"{', '.join(c.refute_sessions)})"
            )
        console.print(f"(dry-run) {len(closures)} candidate(s); nothing mutated")
        return
    for c in closures:
        apply_closure(store, c)
    console.print(f"[green]✓[/green] closed: {', '.join(c.hyp_id for c in closures)}")


@hyp.command("append-verdict")
@click.option("--hyp", "hyp_id", required=True, help="Target hypothesis (HYP-NNN)")
@click.option("--session", "session_id", required=True, help="Source session id")
@click.option(
    "--verdict",
    type=click.Choice(["confirmed", "refuted", "inconclusive", "n/a"]),
    required=True,
)
@click.option("--evidence", required=True, help="One-line citation of evidence")
@click.option(
    "--recommendation",
    type=click.Choice(["close_confirmed", "close_refuted", "keep", "extend_window"]),
    default="keep",
)
@click.option(
    "--date",
    "entry_date",
    default=None,
    help="Verdict date (YYYY-MM-DD); defaults to today (UTC)",
)
def hyp_append_verdict(
    hyp_id: str,
    session_id: str,
    verdict: str,
    evidence: str,
    recommendation: str,
    entry_date: str | None,
):
    """Atomically append a ValidationLogEntry to HYP-NNN.yaml."""
    store = _store()
    if not store.path(hyp_id).exists():
        raise click.ClickException(f"{hyp_id} not found at {store.path(hyp_id)}")
    d = date.fromisoformat(entry_date) if entry_date else datetime.now(tz=timezone.utc).date()
    entry = ValidationLogEntry(
        date=d,
        verdict=verdict,  # type: ignore[arg-type]
        evidence=evidence,
        recommendation=recommendation,  # type: ignore[arg-type]
        session_id=session_id,
        timestamp=datetime.now(tz=timezone.utc),
    )
    h = store.append_verdict(hyp_id, entry)
    console.print(
        f"[green]✓[/green] {hyp_id}: appended {verdict} verdict "
        f"({len(h.validation_log)} entries total)"
    )


@hyp.command("migrate")
@click.option("--dry-run", is_flag=True, help="Print what would change")
def hyp_migrate(dry_run: bool):
    """One-shot normalize all HYP-*.yaml under current schema (idempotent)."""
    project_dir = _project_dir()
    d = _hyp_dir(project_dir)
    if not d.exists():
        raise click.ClickException(f"No hypotheses dir at {d}")

    changed = 0
    for p in sorted(d.iterdir()):
        if p.suffix.lower() not in {".yaml", ".yml"} or not p.name.startswith("HYP-"):
            continue
        raw = yaml.safe_load(p.read_text()) or {}
        normalized = _normalize_hyp_dict(raw)
        if normalized == raw:
            continue
        changed += 1
        if dry_run:
            console.print(f"[yellow]would change[/yellow] {p.name}")
        else:
            atomic_write_text(p, yaml.safe_dump(normalized, sort_keys=False, allow_unicode=True))
            console.print(f"[green]✓[/green] {p.name}")

    # Ensure proposals dir + .gitkeep exists for downstream PROP CLI.
    from ..paths import proposals_dir

    proposals = proposals_dir(project_dir)
    if not dry_run:
        proposals.mkdir(parents=True, exist_ok=True)
        gk = proposals / ".gitkeep"
        if not gk.exists():
            gk.write_text("")

    console.print(
        f"\nMigration {'(dry-run) ' if dry_run else ''}complete: {changed} file(s) changed"
    )


def _normalize_hyp_dict(raw: dict) -> dict:
    """Apply defaults and structure conversions; idempotent.

    Adds missing fields with sensible defaults, normalizes validation_log
    entries to ValidationLogEntry-compatible shape (best-effort), preserves
    all unknown legacy keys.
    """
    out = dict(raw)
    out.setdefault("min_sessions_per_bundle", 4)

    # exit_criteria: ensure dict shape if absent
    if "exit_criteria" not in out:
        out["exit_criteria"] = {"confirm": [], "refute": [], "stalled": []}
    elif isinstance(out.get("exit_criteria"), dict):
        ec = out["exit_criteria"]
        for k in ("confirm", "refute", "stalled"):
            ec.setdefault(k, [])

    # validation_log: convert legacy free-form entries best-effort
    if "validation_log" not in out:
        out["validation_log"] = []
    else:
        log = out["validation_log"] or []
        out["validation_log"] = [_normalize_log_entry(e) for e in log]

    # validate via pydantic (drops bad shapes early; raises on hard errors)
    Hypothesis.model_validate(out)
    return out


def _normalize_log_entry(entry: dict) -> dict:
    """Best-effort migrate a legacy validation_log entry to ValidationLogEntry shape.

    Old entries used keys like {date, bundle, sweep_report, verdict, sample, ...}.
    New requires {date, verdict, evidence, recommendation}. Preserve extras.
    """
    if not isinstance(entry, dict):
        return entry  # type: ignore[return-value]
    out = dict(entry)
    # Synthesize required fields from legacy data.
    if "verdict" not in out:
        out["verdict"] = "inconclusive"
    if out["verdict"] not in {"confirmed", "refuted", "inconclusive", "n/a"}:
        out["verdict"] = "inconclusive"
    if "evidence" not in out or not str(out.get("evidence", "")).strip():
        # Try to synthesize from common legacy fields.
        candidate = (
            out.get("sweep_report")
            or out.get("bundle")
            or out.get("notes")
            or "(legacy entry — no evidence captured)"
        )
        out["evidence"] = str(candidate)
    out.setdefault("recommendation", "keep")
    if out["recommendation"] not in {"close_confirmed", "close_refuted", "keep", "extend_window"}:
        out["recommendation"] = "keep"
    if "date" not in out:
        out["date"] = date.today().isoformat()
    # Validate strict shape against ValidationLogEntry (extras allowed).
    ValidationLogEntry.model_validate(out)
    return out
