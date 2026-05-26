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
    under the harness's per-session namespace
    (`<project>/.gitlog/pipeline_runs/reflect-role/<session_id>/
    composed/<target_role>/`; HATS-308 — each invocation owns its
    own `<session_id>/` subdir, so parallel runs do not race). The
    `reflect-role` pipeline
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
from ..paths import hypotheses_dir, proposals_dir
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

    from ..paths import runs_dir

    project_dir = _project_dir()
    log_path = runs_dir(project_dir) / f"session_{session_id}" / "retro.log"
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
    from ..assembler import Assembler
    from ..pipeline.harness import PipelineHarness

    project_dir = _project_dir()
    handoff_path = _build_handoff(project_dir)
    console.print(f"[green]✓[/green] Handoff written: {handoff_path}")
    if dry_run:
        return

    preamble_path = Assembler(project_dir).resolver.resolve_injection(
        "reflect-all",
    )
    if preamble_path is None:
        raise click.ClickException(
            "built-in initial_injection 'reflect-all' not found in any "
            "library_path — ai-hats packaging is broken"
        )
    preamble = preamble_path.read_text()
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


# ---- reflect hypothesis (HATS-513: 2-phase judge split) ----


@reflect.command("hypothesis")
@click.option(
    "--headless", is_flag=True,
    help="Phase 1 only — produce draft, exit. No HITL session.",
)
@click.option(
    "--dry-run", is_flag=True,
    help="Build pre-flight handoff but do not exec.",
)
def reflect_hypothesis_cmd(headless: bool, dry_run: bool):
    """Two-phase HYP closure + PROP triage (HATS-513 / ADR-0007).

    Phase 1 (`judge-auditor`, headless, read-only via SubAgentRunner)
    produces a draft with proposed verdicts and proposed CLI mutations.
    Phase 2 (`judge`, HITL via WrapRunner) consumes the draft, supervisor
    ack's mutations, judge executes them via CLI and writes the final
    report.

    With ``--headless``: only Phase 1 runs (CI / cron-safe, no
    state-mutating CLI calls possible by L0 contract).
    """
    from ..assembler import Assembler
    from ..pipeline.harness import PipelineHarness

    project_dir = _project_dir()
    handoff_path = _build_handoff(project_dir)
    console.print(f"[green]✓[/green] Handoff written: {handoff_path}")
    if dry_run:
        return

    resolver = Assembler(project_dir).resolver

    # ── Phase 1 — judge-auditor (read-only audit) ─────────────────
    preamble1_path = resolver.resolve_injection("reflect-hypothesis")
    if preamble1_path is None:
        raise click.ClickException(
            "built-in initial_injection 'reflect-hypothesis' not found in any "
            "library_path — ai-hats packaging is broken"
        )
    preamble1 = preamble1_path.read_text()
    handoff_text = handoff_path.read_text()
    combined1 = f"{preamble1}\n\n---\n\n{handoff_text}"

    console.print(
        "[cyan]→ Phase 1 — judge-auditor (headless audit)[/]"
    )
    with PipelineHarness("reflect-hypothesis-phase1", project_dir) as h1:
        r1 = h1.run({
            "role": "judge-auditor",
            "interactive": False,
            "project_dir": project_dir,
            "prompt_path": h1.materialize_prompt(combined1),
            "extra_args": [],
        })

    # Fail closed: Phase 1 errored or did not produce a draft.
    # Opening Phase 2 on a missing/partial artifact would mislead the
    # supervisor — abort with a non-zero exit instead.
    if int(r1.get("exit_code", 1)) != 0 or "saved_path" not in r1:
        console.print(
            "[red]✗[/] Phase 1 (judge-auditor) failed — Phase 2 aborted."
        )
        sys.exit(int(r1.get("exit_code", 1)) or 1)

    draft_path = Path(r1["saved_path"])
    console.print(f"[green]✓[/] Phase 1 draft: {draft_path}")
    if headless:
        sys.exit(0)

    # ── Phase 2 — judge (HITL session with draft inlined) ─────────
    # Empty draft (only "(none)" sections) is fine — supervisor
    # decides nothing-to-do in 1 turn. No auto-skip on success.
    preamble2_path = resolver.resolve_injection("reflect-hypothesis-interactive")
    if preamble2_path is None:
        raise click.ClickException(
            "built-in initial_injection 'reflect-hypothesis-interactive' not found"
        )
    preamble2 = preamble2_path.read_text()
    combined2 = preamble2.replace("{draft_body}", draft_path.read_text())

    console.print(
        "[cyan]→ Phase 2 — judge (HITL session with draft inlined)[/]"
    )
    with PipelineHarness("reflect-hypothesis-phase2", project_dir) as h2:
        r2 = h2.run({
            "role": "judge",
            "interactive": True,
            "project_dir": project_dir,
            "prompt_path": h2.materialize_prompt(combined2),
            "extra_args": [],
        })
    sys.exit(int(r2.get("exit_code", 1)))


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

    assembler = Assembler(project_dir)
    composer = assembler.composer
    # HATS-505: deliberately no ``overlays=`` — ``reflect`` shows the
    # role's BUILT-IN composition for inspection (what the library
    # ships), excluding the project / global overlay layering that
    # runtime consumers apply. This is the whole point of ``reflect``;
    # using ``compose_for_role`` here would conflate "what does the
    # role contain" with "what would my project see after overlays".
    # Out of scope for both drift tests in
    # ``tests/test_no_direct_compose_outside_facade.py``:
    # ``test_compose_with_overlays_only_in_facade`` matches the
    # ``overlays=`` form only and this call has none;
    # ``test_no_direct_compose_inside_pipeline_subtree`` scans
    # ``src/ai_hats/pipeline/`` only and this file is under ``cli/``.
    composition = composer.compose(target_role)
    if composition.errors:
        raise click.ClickException(
            f"Cannot compose role {target_role!r}: {composition.errors}"
        )

    preamble_path = assembler.resolver.resolve_injection("reflect-role")
    if preamble_path is None:
        raise click.ClickException(
            "built-in initial_injection 'reflect-role' not found in any "
            "library_path — ai-hats packaging is broken"
        )
    preamble_template = preamble_path.read_text()

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
        # Role-mirror is a publish artefact regenerated from the live
        # composition on every `ai-hats reflect` — recovery is `ai-hats
        # reflect` itself. Whitelist.
        shutil.rmtree(target_dir)  # safe-delete: ok reflect-republish
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
    """Compose the prompt fed to the `hypothesis-intake` role.

    Includes the most recent ``validation_log`` evidences per HYP so the
    dedup judgment sees how the hypothesis has been used in practice, not
    just its (potentially drifted) one-line statement.
    """
    import json as _json

    payload = []
    for h in active_hyps:
        item = {
            "id": h.id,
            "title": h.title,
            "hypothesis": h.hypothesis,
        }
        recent = [
            e.evidence for e in h.validation_log[-3:] if e.evidence
        ]
        if recent:
            item["recent_evidence"] = recent
        payload.append(item)
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

    store = HypothesisStore(hypotheses_dir(project_dir))
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


def _spawn_intake_detached(
    text: str, session_id: str | None, task_id: str | None,
) -> tuple[int, Path]:
    """Re-invoke ``ai-hats reflect issue`` as a detached process.

    Returns ``(pid, log_path)``.

    The child runs in foreground default mode (no preview, no --bg) so it
    writes the intake when the pipeline returns. Output is appended to a
    timestamped log under ``<ai_hats_dir>/sessions/runs/reflect-issue/``.
    """
    import subprocess
    from datetime import datetime, timezone

    from ..paths import runs_dir

    project_dir = _project_dir()
    log_dir = runs_dir(project_dir) / "reflect-issue"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = log_dir / f"{ts}-bg.log"

    cmd = [
        sys.executable, "-c",
        "from ai_hats.cli import main_entry; main_entry()",
        "reflect", "issue", text,
    ]
    if session_id:
        cmd += ["--session", session_id]
    if task_id:
        cmd += ["--task", task_id]

    with open(log_path, "a") as f:
        f.write(f"--- reflect issue (bg) {ts} ---\n")
        f.write(f"observation: {text}\n\n")
        f.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=str(project_dir),
            stdout=f,
            stderr=f,
            start_new_session=True,
        )
    return proc.pid, log_path


@reflect.command("issue")
@click.argument("text")
@click.option(
    "--preview", "-n", "preview_mode", is_flag=True,
    help="Show the intake draft and prompt before writing.",
)
@click.option(
    "--bg", "--background", "background", is_flag=True,
    help="Run detached; return immediately. Output goes to .gitlog/reflect-issue/.",
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
    preview_mode: bool,
    background: bool,
    session_id: str | None,
    task_id: str | None,
) -> None:
    """Log a supervisor observation as a hypothesis (create or merge).

    Runs the `reflect-issue` pipeline: Haiku reads the observation +
    the list of active HYPs, decides whether to draft a new HYP
    (``action: create``) or append the observation as evidence to an
    existing HYP (``action: merge``).

    By default writes immediately on success. Use ``--preview`` to inspect
    the draft and confirm interactively, or ``--bg`` to detach.
    """
    from ..hypothesis import IntakeParseError, parse_intake_yaml

    if background and preview_mode:
        raise click.ClickException(
            "--bg and --preview are mutually exclusive"
        )

    if background:
        pid, log_path = _spawn_intake_detached(text, session_id, task_id)
        console.print(
            f"[dim]reflect issue spawned (pid={pid}, bg) → {log_path}[/dim]"
        )
        return

    project_dir = _project_dir()
    store = HypothesisStore(hypotheses_dir(project_dir))
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

    if preview_mode:
        preview = _format_preview(action)
        console.print("[bold]Intake draft:[/bold]")
        click.echo(preview)
        if degraded:
            click.echo("(degraded — title/hypothesis only)", err=True)
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
    store = ProposalStore(proposals_dir(project_dir))
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
    from ..paths import retros_dir

    return retros_dir(project_dir) / "reflect-all"


def _build_handoff(project_dir: Path) -> Path:
    """Collect active HYP + open PROP into a single markdown handoff file."""
    hstore = HypothesisStore(hypotheses_dir(project_dir))
    pstore = ProposalStore(proposals_dir(project_dir))
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
            # HATS-534 — surface verification_protocol when present (stored
            # via Hypothesis.extra="allow"). Literal block scalar so
            # multi-line protocols stay verbatim for judge / review-hypothesis
            # Step 1.5 consumption.
            vp = getattr(h, "verification_protocol", None)
            if vp:
                indented = "\n".join(
                    f"    {line}" for line in str(vp).splitlines()
                )
                parts.append(f"- verification_protocol: |\n{indented}")
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
