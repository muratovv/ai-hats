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
    composes the target role and materializes its layered breakdown
    under the harness namespace (`<project>/.gitlog/pipeline_runs/
    reflect-role/composed/<target_role>/`). The `reflect-role` pipeline
    then launches `auditor-for-role` interactively with a small prompt
    that points at those files; the reviewer reads them via Read/Glob
    tools as needed. The audit report is persisted under
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
    """Materialize the target role's layered breakdown and run reflect-role.

    The reviewer reads the composed files (and ./CLAUDE.md, user-rules)
    through Read/Glob tools during the interactive session, instead of
    receiving everything inlined in the prompt.
    """
    from ..assembler import Assembler
    from ..pipeline.harness import PipelineHarness
    from .execute import _initial_injections_dir

    composer = Assembler(project_dir).composer
    composition = composer.compose(target_role)
    if composition.errors:
        raise click.ClickException(
            f"Cannot compose role {target_role!r}: {composition.errors}"
        )

    preamble_template = (
        _initial_injections_dir() / "reflect-role.md"
    ).read_text()

    console.print(
        f"[cyan]→ Launching auditor-for-role to audit: {target_role}[/]"
    )
    with PipelineHarness("reflect-role", project_dir) as h:
        composed_dir = _materialize_target_composition(
            h.namespace / "composed", composition, target_role,
        )
        preamble = preamble_template.format(
            target_role=target_role,
            composed_dir=composed_dir,
            project_dir=project_dir,
        )
        final = h.run({
            "role": "auditor-for-role",
            "target_role": target_role,
            "interactive": True,
            "project_dir": project_dir,
            "prompt_path": h.materialize_prompt(preamble),
            "extra_args": [],
        })
    saved = final.get("saved_path")
    if saved:
        console.print(f"[green]✓[/green] reflect saved to {saved}")
    return final


def _materialize_target_composition(
    base_dir: Path, composition, target_role: str,
) -> Path:
    """Write the composition's layered breakdown to ``base_dir/<role>/``.

    Layout (everything the reviewer needs to trace findings to source):

        manifest.yaml          # name, priorities, traits/rules/skills
        role-injection.md      # role's own injection (if non-empty)
        overlay-injection.md   # overlay text (if any)
        traits/<name>.md       # per-trait injection texts
        rules/<name>.md        # bundled rule bodies
        skills/<name>.md       # bundled skill bodies

    Returns the role-specific directory path.
    """
    import shutil

    import yaml

    target_dir = base_dir / target_role
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True)

    manifest = {
        "name": composition.name,
        "priorities": list(composition.priorities),
        "composition": {
            "traits": list(composition.trait_injections.keys()),
            "rules": [r.name for r in composition.rules],
            "skills": [s.name for s in composition.skills],
        },
    }
    (target_dir / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True)
    )

    if composition.role_injection:
        (target_dir / "role-injection.md").write_text(
            composition.role_injection
        )
    if composition.overlay_injection:
        (target_dir / "overlay-injection.md").write_text(
            composition.overlay_injection
        )

    if composition.trait_injections:
        traits_dir = target_dir / "traits"
        traits_dir.mkdir()
        for name, text in composition.trait_injections.items():
            (traits_dir / f"{name}.md").write_text(text)

    if composition.rules:
        rules_dir = target_dir / "rules"
        rules_dir.mkdir()
        for r in composition.rules:
            (rules_dir / f"{r.name}.md").write_text(r.injection or "")

    if composition.skills:
        skills_dir = target_dir / "skills"
        skills_dir.mkdir()
        for s in composition.skills:
            (skills_dir / f"{s.name}.md").write_text(s.injection or "")

    return target_dir


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
