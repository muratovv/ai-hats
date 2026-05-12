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
    then launches `judge-for-role` interactively with a small prompt
    that points at those files; the judge reads them via Read/Glob
    tools as needed and writes the audit report directly via the
    Write tool to
    `.agent/retrospectives/role-coherence/<UTC-ts>-<target>.md`.
    The audit protocol lives in the `role-coherence-protocol` skill
    (HATS-263); the judge dialogue contract lives in
    `judge-role-protocol` (HATS-296, HATS-297).

- `reflect commit ...`
    Bulk-update proposal statuses (called at end of interactive chat).
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from ..hypothesis import (
    HypothesisStore,
    ProposalStore,
)
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
        f"[cyan]→ Launching judge-for-role to audit: {target_role}[/]"
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
            "role": "judge-for-role",
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


# ---- reflect issue ----


SUPERVISOR_SOURCE_TASK = "supervisor-observation"
INTAKE_MODEL = "haiku"


def _build_intake_prompt(text: str, active_hyps: list) -> str:
    """Compose the prompt fed to the `hypothesis-intake` role."""
    import json as _json

    payload = [
        {"id": h.id, "title": h.title, "hypothesis": h.hypothesis}
        for h in active_hyps
    ]
    return (
        "OBSERVATION:\n"
        f"{text.strip()}\n"
        "\n---\n\n"
        "ACTIVE_HYPOTHESES:\n"
        f"{_json.dumps(payload, ensure_ascii=False, indent=2)}\n"
    )


def _run_intake_pipeline(
    project_dir: Path, prompt_text: str,
) -> tuple[str, int]:
    """Invoke `reflect-issue` pipeline; return (intake_result_text, exit_code).

    Empty ``intake_result_text`` means the marker block was missing in the
    transcript. Caller treats that as a pipeline failure.
    """
    from ..pipeline.harness import PipelineHarness

    with PipelineHarness("reflect-issue", project_dir) as h:
        final = h.run({
            "role": "hypothesis-intake",
            "interactive": False,
            "project_dir": project_dir,
            "prompt_path": h.materialize_prompt(prompt_text),
            "model": INTAKE_MODEL,
        })
    return (
        final.get("intake_result", "") or "",
        int(final.get("exit_code", 1)),
    )


def _minimal_create_action(text: str):
    """Build a degraded CreateAction with only title+hypothesis populated.

    Used when the LLM round-trip fails AND there are no active HYPs (so we
    cannot deduplicate). The supervisor still gets a HYP file to edit; all
    schema-optional fields are left empty for a later pass.
    """
    from ..hypothesis import CreateAction, IntakeDraft

    title = text.strip().splitlines()[0][:60] or "supervisor observation"
    return CreateAction(
        action="create",
        draft=IntakeDraft(title=title, hypothesis=text.strip()),
    )


def _format_preview(action) -> str:
    """Pretty-print an IntakeResult for the interactive confirmation prompt."""
    import yaml as _yaml

    from ..hypothesis import CreateAction, MergeAction

    if isinstance(action, MergeAction):
        body = {
            "action": "merge",
            "target_id": action.target_id,
            "evidence": action.evidence,
        }
    elif isinstance(action, CreateAction):
        body = {
            "action": "create",
            "draft": action.draft.model_dump(exclude_none=True),
        }
    else:  # pragma: no cover — defensive
        return str(action)
    return _yaml.safe_dump(body, sort_keys=False, allow_unicode=True)


def _write_intake(
    project_dir: Path,
    action,
    *,
    text: str,
    session_id: str | None,
    task_id: str | None,
) -> str:
    """Materialize the intake decision via HypothesisStore. Returns HYP id."""
    from datetime import date, datetime, timezone

    from ..hypothesis import (
        CreateAction,
        ExitCriteria,
        Hypothesis,
        MergeAction,
        ValidationLogEntry,
        next_hypothesis_id,
    )

    store = HypothesisStore(project_dir / ".agent" / "hypotheses")
    store.dir.mkdir(parents=True, exist_ok=True)

    if isinstance(action, MergeAction):
        target_path = store.path(action.target_id)
        if not target_path.exists():
            raise click.ClickException(
                f"intake returned merge target {action.target_id} "
                "but the file does not exist; refusing to fabricate"
            )
        entry = ValidationLogEntry(
            date=datetime.now(tz=timezone.utc).date(),
            verdict="inconclusive",
            evidence=action.evidence,
            recommendation="keep",
            session_id=session_id,
            timestamp=datetime.now(tz=timezone.utc),
        )
        store.append_verdict(action.target_id, entry)
        return action.target_id

    if isinstance(action, CreateAction):
        new_id = next_hypothesis_id(store.dir)
        d = action.draft
        ec = None
        if d.exit_criteria:
            ec = ExitCriteria(
                confirm=list(d.exit_criteria.get("confirm") or []),
                refute=list(d.exit_criteria.get("refute") or []),
                stalled=list(d.exit_criteria.get("stalled") or []),
            )
        h = Hypothesis(
            id=new_id,
            title=d.title,
            status="active",
            created=date.today(),
            source_task=task_id or SUPERVISOR_SOURCE_TASK,
            hypothesis=d.hypothesis,
            baseline=d.baseline,
            expected_outcome=list(d.expected_outcome),
            success_criterion=d.success_criterion,
            exit_criteria=ec,
        )
        store.create(h)
        return new_id

    raise click.ClickException(  # pragma: no cover — defensive
        f"unexpected intake action type: {type(action).__name__}"
    )


@reflect.command("issue")
@click.argument("text")
@click.option(
    "--confirm", "--yes", "auto_confirm", is_flag=True,
    help="Write without the interactive y/N prompt.",
)
@click.option(
    "--session", "session_id", default=None,
    help="Source session id (YYYYMMDD-HHMMSS-N) — recorded on merge.",
)
@click.option(
    "--task", "task_id", default=None,
    help="Originating task id (e.g., HATS-NNN); defaults to 'supervisor-observation'.",
)
def reflect_issue_cmd(
    text: str,
    auto_confirm: bool,
    session_id: str | None,
    task_id: str | None,
) -> None:
    """Log a supervisor observation as a hypothesis (create or merge).

    Runs the `reflect-issue` pipeline: Haiku reads the observation +
    the list of active HYPs, decides whether to draft a new HYP
    (``action: create``) or append the observation as evidence to an
    existing HYP (``action: merge``).
    """
    from ..hypothesis import IntakeParseError, parse_intake_yaml

    project_dir = _project_dir()
    store = HypothesisStore(project_dir / ".agent" / "hypotheses")
    active = store.list_active()
    prompt_text = _build_intake_prompt(text, active)

    action = None
    degraded = False
    try:
        intake_text, exit_code = _run_intake_pipeline(project_dir, prompt_text)
        if exit_code != 0:
            raise RuntimeError(
                f"reflect-issue pipeline exited non-zero ({exit_code})"
            )
        if not intake_text:
            raise RuntimeError(
                "reflect-issue pipeline did not emit "
                "BEGIN_INTAKE_RESULT/END_INTAKE_RESULT block"
            )
        action = parse_intake_yaml(intake_text)
    except (RuntimeError, IntakeParseError) as exc:
        if active:
            raise click.ClickException(
                f"intake failed and active hypotheses exist — refusing to "
                f"create without dedup. Cause: {exc}"
            ) from exc
        click.echo(
            f"⚠ intake LLM call failed ({exc}); falling back to minimal HYP",
            err=True,
        )
        action = _minimal_create_action(text)
        degraded = True

    if isinstance(action, type(None)):  # pragma: no cover — defensive
        raise click.ClickException("intake produced no action")

    preview = _format_preview(action)
    console.print("[bold]Intake draft:[/bold]")
    click.echo(preview)
    if degraded:
        click.echo("(degraded — title/hypothesis only)", err=True)

    if not auto_confirm:
        if not click.confirm("Write this intake?", default=False):
            console.print("[yellow]aborted; nothing written[/yellow]")
            return

    hyp_id = _write_intake(
        project_dir, action,
        text=text, session_id=session_id, task_id=task_id,
    )
    from ..hypothesis import MergeAction

    if isinstance(action, MergeAction):
        console.print(
            f"[green]✓[/green] merged into {hyp_id} (validation_log +1)"
        )
    else:
        console.print(f"[green]✓[/green] created {hyp_id} (status=active)")


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
