"""`ai-hats reflect` — group command for the per-session and bulk-triage flows.

Subcommands:

- `reflect session [--session ID] [--background]`
    Run the reflect-session role on one session. Auto-trigger from
    session-end uses --background to detach from the caller.

- `reflect all [--dry-run]`
    Pre-flight (Python) builds a handoff under
    `.agent/retrospectives/reflect-all/<ts>-handoff.md`, then forwards to
    `ai-hats execute --role judge --interactive` with a combined prompt
    (initial-injections preamble + handoff). The triage protocol itself
    lives in the `judge-protocol` skill, not in this command.

- `reflect role <name>` / `reflect roles`
    Audit a target role for coherence against the user's project context
    (./CLAUDE.md + .agent/ai-hats/user-rules/*.md). Pre-flight (Python)
    composes the target role in-memory and assembles the full audit
    prompt; the `reflect-role` pipeline then launches `role_reviewer`
    interactively and persists the report under
    `.agent/retrospectives/reflect/<target>-<ts>.md`. The audit protocol
    lives in the `role-coherence-protocol` skill (HATS-263).

- `reflect commit ...`
    Bulk-update proposal statuses (called at end of interactive chat).
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from ..hypothesis import HypothesisStore, ProposalStore
from ..retro.session_review_runner import SessionReviewError
from ._helpers import _project_dir, console


@click.group("reflect")
def reflect():
    """Per-session and bulk-triage flows for hypotheses + proposals."""


# ---- reflect session ----


@reflect.command("session")
@click.option(
    "--session", "session_id", required=True,
    help="Session id (YYYYMMDD-HHMMSS-N) to reflect on",
)
@click.option(
    "--background", is_flag=True,
    help="Run as detached background process (used by auto-trigger).",
)
@click.option(
    "--max-retries", type=int, default=1, show_default=True,
)
def reflect_session_cmd(session_id: str, background: bool, max_retries: int):
    """Run session-reviewer on one session and validate output.

    On any failure the harness layer files a meta-proposal — the command still
    exits non-zero so the caller can react, but the proposal serves as the
    durable audit record.
    """
    if background:
        _spawn_detached(session_id, max_retries)
        return

    from ..pipeline.harness import PipelineHarness

    project_dir = _project_dir()
    try:
        with PipelineHarness("reflect-session", project_dir) as h:
            final = h.run({
                "session_id": session_id,
                "project_dir": project_dir,
                "max_retries": max_retries,
            })
    except SessionReviewError as exc:
        console.print(
            f"[yellow]session-reviewer failed for {session_id}:[/yellow] {exc}\n"
            "Run via `--background` to also engage the harness check; or re-run "
            "after addressing the cause."
        )
        sys.exit(2)
    else:
        console.print(
            f"[green]✓[/green] session review saved to {final['review_path']}"
        )


def _spawn_detached(session_id: str, max_retries: int) -> None:
    """Re-invoke ourselves in a new process group, return immediately."""
    import subprocess

    project_dir = _project_dir()
    log_path = (
        project_dir / ".gitlog" / f"session_{session_id}" / "retro.log"
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as f:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m", "ai_hats.cli.reflect_session_main",
                session_id,
                str(max_retries),
            ],
            cwd=str(project_dir),
            stdout=f,
            stderr=f,
            start_new_session=True,
        )
    console.print(f"[dim]reflect session spawned (pid={proc.pid}, bg)[/dim]")


# ---- reflect all ----


@reflect.command("all")
@click.option(
    "--dry-run", is_flag=True,
    help="Build pre-flight handoff but do not exec claude.",
)
def reflect_all_cmd(dry_run: bool):
    """Interactive HYP closure + proposal triage via the `judge` role."""
    from ..pipeline.harness import PipelineHarness
    from .execute import _initial_injections_dir

    project_dir = _project_dir()
    handoff_path = _build_handoff(project_dir)
    console.print(f"[green]✓[/green] Handoff written: {handoff_path}")
    if dry_run:
        return

    preamble = (_initial_injections_dir() / "reflect-all.md").read_text()
    handoff_text = handoff_path.read_text()
    combined = f"{preamble}\n\n---\n\n{handoff_text}"

    console.print(
        f"[cyan]→ Launching judge for reflect-all triage: {handoff_path}[/]"
    )
    with PipelineHarness("reflect-all", project_dir) as h:
        final = h.run({
            "role": "judge",
            "interactive": True,
            "project_dir": project_dir,
            "prompt_path": h.materialize_prompt(combined),
            "extra_args": [],
        })
    sys.exit(int(final.get("exit_code", 1)))


# ---- reflect role / reflect roles ----


@reflect.command("role")
@click.argument("name")
def reflect_role_cmd(name: str):
    """Audit a single role against the project context for coherence."""
    project_dir = _project_dir()
    final = _run_role_audit(project_dir, name)
    sys.exit(int(final.get("exit_code", 1)))


@reflect.command("roles")
def reflect_roles_cmd():
    """Audit every role available to this project, one session per role."""
    from ..assembler import Assembler
    from ..models import ComponentType

    project_dir = _project_dir()
    resolver = Assembler(project_dir).resolver
    names = resolver.list_components(ComponentType.ROLE)
    if not names:
        console.print("[yellow]No roles found in library.[/yellow]")
        sys.exit(1)

    console.print(
        f"[cyan]→ {len(names)} role(s) to audit: {', '.join(names)}[/]"
    )
    worst_exit = 0
    for n in names:
        console.print(f"\n[bold cyan]── reflect role {n} ──[/]")
        final = _run_role_audit(project_dir, n)
        ec = int(final.get("exit_code", 1))
        if ec != 0 and worst_exit == 0:
            worst_exit = ec
    sys.exit(worst_exit)


def _run_role_audit(project_dir: Path, target_role: str) -> dict:
    """Compose target role + project context, then run reflect-role pipeline."""
    from ..assembler import Assembler
    from ..pipeline.harness import PipelineHarness
    from .execute import _initial_injections_dir

    composer = Assembler(project_dir).composer
    composition = composer.compose(target_role)
    if composition.errors:
        raise click.ClickException(
            f"Cannot compose role {target_role!r}: {composition.errors}"
        )

    audit_view = _render_role_audit_view(composition, target_role)
    claude_md_path = project_dir / "CLAUDE.md"
    claude_md = claude_md_path.read_text() if claude_md_path.exists() else ""
    user_rules_dir = project_dir / ".agent" / "ai-hats" / "user-rules"
    user_rules_parts: list[str] = []
    if user_rules_dir.is_dir():
        for f in sorted(user_rules_dir.glob("*.md")):
            user_rules_parts.append(f"### {f.stem}\n\n{f.read_text()}")
    user_rules_text = "\n\n".join(user_rules_parts)

    preamble = (_initial_injections_dir() / "reflect-role.md").read_text()
    combined = "\n\n---\n\n".join([
        preamble,
        audit_view,
        f"## Project CLAUDE.md\n\n{claude_md or '(none)'}",
        f"## User rules overlay\n\n{user_rules_text or '(none)'}",
    ])

    console.print(
        f"[cyan]→ Launching role_reviewer to audit: {target_role}[/]"
    )
    with PipelineHarness("reflect-role", project_dir) as h:
        final = h.run({
            "role": "role_reviewer",
            "target_role": target_role,
            "interactive": True,
            "project_dir": project_dir,
            "prompt_path": h.materialize_prompt(combined),
            "extra_args": [],
        })
    saved = final.get("saved_path")
    if saved:
        console.print(f"[green]✓[/green] reflect saved to {saved}")
    return final


def _render_role_audit_view(composition, target_role: str) -> str:
    """Render the layered audit view of a composed role.

    Unlike ``CompositionResult.merged_injection`` (which only joins trait
    + role injection texts), this view exposes every layer the reviewer
    needs: priorities, composition manifest, per-trait injections, role
    injection, bundled rule bodies, bundled skill bodies. Reviewer can
    then trace each instruction to its source component.
    """
    parts: list[str] = [f"## Target role audit view: {target_role}"]

    if composition.priorities:
        parts.append("### Priorities")
        parts.append(
            "\n".join(
                f"{i}. {p}" for i, p in enumerate(composition.priorities, 1)
            )
        )

    parts.append("### Composition manifest")
    trait_names = list(composition.trait_injections.keys())
    parts.append(
        f"- Traits: {', '.join(trait_names) if trait_names else '(none)'}\n"
        f"- Rules: "
        f"{', '.join(r.name for r in composition.rules) or '(none)'}\n"
        f"- Skills: "
        f"{', '.join(s.name for s in composition.skills) or '(none)'}"
    )

    if composition.trait_injections:
        parts.append("### Trait injections")
        for name, text in composition.trait_injections.items():
            parts.append(f"#### trait: {name}\n\n{text}")

    if composition.role_injection:
        parts.append(
            f"### Role injection: {target_role}\n\n{composition.role_injection}"
        )

    if composition.overlay_injection:
        parts.append(
            f"### Overlay injection\n\n{composition.overlay_injection}"
        )

    if composition.rules:
        parts.append("### Bundled rules")
        for r in composition.rules:
            body = r.injection.strip() or "(empty rule body)"
            parts.append(f"#### rule: {r.name}\n\n{body}")

    if composition.skills:
        parts.append("### Bundled skills")
        for s in composition.skills:
            body = s.injection.strip() or "(empty skill body)"
            parts.append(f"#### skill: {s.name}\n\n{body}")

    if not (
        composition.priorities
        or composition.trait_injections
        or composition.role_injection
        or composition.rules
        or composition.skills
    ):
        parts.append("(empty composition — nothing to audit)")

    return "\n\n".join(parts)


# ---- reflect commit ----


@reflect.command("commit")
@click.option(
    "--accept", multiple=True, help="PROP-NNN to mark accepted (repeatable)",
)
@click.option(
    "--reject", multiple=True, help="PROP-NNN to mark rejected (repeatable)",
)
@click.option(
    "--defer", multiple=True, help="PROP-NNN to mark deferred (repeatable)",
)
@click.option(
    "--duplicate", multiple=True, help="PROP-NNN to mark duplicate (repeatable)",
)
def reflect_commit_cmd(accept, reject, defer, duplicate):
    """Bulk-update proposal statuses (called at end of interactive chat)."""
    project_dir = _project_dir()
    store = ProposalStore(project_dir / ".agent" / "backlog" / "proposals")
    changes = 0
    for pid in accept:
        store.set_status(pid, "accepted")
        console.print(f"  {pid} → accepted")
        changes += 1
    for pid in reject:
        store.set_status(pid, "rejected")
        console.print(f"  {pid} → rejected")
        changes += 1
    for pid in defer:
        store.set_status(pid, "deferred")
        console.print(f"  {pid} → deferred")
        changes += 1
    for pid in duplicate:
        store.set_status(pid, "duplicate")
        console.print(f"  {pid} → duplicate")
        changes += 1
    console.print(f"[green]✓[/green] reflect commit: {changes} change(s)")


# ---- pre-flight handoff (used by `reflect all`) ----


def _handoff_dir(project_dir: Path) -> Path:
    return project_dir / ".agent" / "retrospectives" / "reflect-all"


def _build_handoff(project_dir: Path) -> Path:
    """Collect active HYP + open PROP into a single markdown handoff file."""
    hstore = HypothesisStore(project_dir / ".agent" / "hypotheses")
    pstore = ProposalStore(project_dir / ".agent" / "backlog" / "proposals")
    active = hstore.list_active()
    open_props = pstore.filter(status="open")

    out_dir = _handoff_dir(project_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    path = out_dir / f"{ts}-handoff.md"

    parts: list[str] = []
    parts.append(f"# reflect-all handoff — {ts}\n")
    parts.append(
        f"Active hypotheses: {len(active)} · "
        f"Open proposals: {len(open_props)}\n"
    )

    parts.append("## Active hypotheses\n")
    if active:
        for h in active:
            parts.append(
                f"### {h.id} — {h.title}\n"
                f"- success_criterion: {h.success_criterion!r}\n"
                f"- observation_window: {h.observation_window!r}\n"
                f"- last_rule_revision_date: {h.last_rule_revision_date}\n"
                f"- validation_log entries: {len(h.validation_log)}\n"
            )
            recent = h.validation_log[-3:]
            if recent:
                parts.append("  Recent verdicts:")
                for e in recent:
                    parts.append(f"  - {e.date} · {e.verdict} · {e.evidence}")
            parts.append("")
    else:
        parts.append("(no active hypotheses)\n")

    parts.append("## Open proposals\n")
    if open_props:
        for p in open_props:
            parts.append(
                f"### {p.id} [{p.category}/{p.target}] — {p.title}\n"
                f"- description: {p.description}\n"
                f"- rationale: {p.rationale}\n"
                f"- votes: {len(p.votes)}\n"
                f"- related_hypotheses: {p.related_hypotheses}\n"
            )
            if p.failed_session_id:
                parts.append(
                    f"- **meta-proposal** failed_session_id: {p.failed_session_id}"
                )
            parts.append("")
    else:
        parts.append("(inbox empty)\n")

    path.write_text("\n".join(parts))
    return path
