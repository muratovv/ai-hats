"""`ai-hats reflect` — group command for the per-session and bulk-triage flows.

Subcommands:

- `reflect session [--session ID] [--background]`
    Run the reflect-session role on one session; the session-end auto-trigger
    uses --background to detach.
- `reflect all [--dry-run]`
    Pre-flight builds a handoff under
    `<ai_hats_dir>/sessions/retros/reflect-all/<ts>-handoff.md`, then forwards to
    `ai-hats execute --role judge --interactive`. The triage protocol lives in
    the `judge-protocol` skill.
- `reflect role <name>` / `reflect roles`
    Audit a target role against the user's project context. Pre-flight composes
    the target and materializes its layered breakdown under a per-session
    namespace (each run owns its `<session_id>/` subdir, so parallel runs don't
    race — HATS-308); the `reflect-role` pipeline launches `role-judge`, which reads
    those files and writes the report itself to
    `<ai_hats_dir>/sessions/retros/role-coherence/<UTC-ISO-ts>-<target>.md` — the
    path is the role's own carve-out, so this pipeline ships no `save_artifact`.
    Protocols: `role-coherence-protocol` + `judge-role-protocol` skills.
- `reflect commit ...`
    Bulk-update proposal statuses (end of interactive chat).
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from ai_hats_observe.artifacts import RETRO_LOG, session_dirname

import click

from ..rack_workspace import (
    active_hypotheses,
    append_verdict,
    create_hypothesis,
    ensure_backlog,
    open_proposals,
    rack_workspace,
    set_proposal_status,
)
from ..pipeline import run_pipeline
from ..pipeline_catalog import (
    REFLECT_ALL,
    REFLECT_HYPOTHESIS_PHASE1,
    REFLECT_HYPOTHESIS_PHASE2,
    REFLECT_ISSUE,
    REFLECT_ROLE,
    REFLECT_SESSION,
)
from ..session_policy import (
    Automate,
    Hitl,
    IntakeOutcome,
    MaterializedRole,
    ReflectSessionRunParams,
    ReportOutcome,
    RoleAudit,
    SessionOutcome,
    SessionRecording,
    SessionReviewOutcome,
    SessionRunParams,
)
from ..retro.session_review_runner import SessionReviewError
from ai_hats_core.layout import ProjectLayout

from ._entry import resolve_project
from ._helpers import console


@click.group("reflect")
def reflect():
    """Per-session and bulk-triage flows for hypotheses + proposals."""


# ---- reflect session ----


@reflect.command("session")
@click.option(
    "--session",
    "session_id",
    required=True,
    help="Session id (YYYYMMDD-HHMMSS-N) to reflect on",
)
@click.option(
    "--background",
    is_flag=True,
    help="Run as detached background process (used by auto-trigger).",
)
@click.option(
    "--max-retries",
    type=int,
    default=1,
    show_default=True,
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

    project_dir = resolve_project().layout.root
    try:
        result = run_pipeline(
            REFLECT_SESSION,
            ReflectSessionRunParams(
                project_dir=project_dir,
                session_id=session_id,
                max_retries=max_retries,
            ),
        )
    except SessionReviewError as exc:
        console.print(
            f"[yellow]session-reviewer failed for {session_id}:[/yellow] {exc}\n"
            "Run via `--background` to also engage the harness check; or re-run "
            "after addressing the cause."
        )
        sys.exit(2)
    else:
        review = SessionReviewOutcome.of(result).require_review_path()
        console.print(f"[green]✓[/green] session review saved to {review}")


def _spawn_detached(session_id: str, max_retries: int) -> None:
    """Re-invoke ourselves in a new process group, return immediately."""
    import subprocess

    layout = resolve_project().layout
    project_dir = layout.root
    log_path = layout.sessions.runs / session_dirname(session_id) / RETRO_LOG
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as f:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "ai_hats.cli.reflect_session_main",
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
    "--dry-run",
    is_flag=True,
    help="Build pre-flight handoff but do not exec claude.",
)
def reflect_all_cmd(dry_run: bool):
    """Interactive HYP closure + proposal triage via the `judge` role."""
    from ..assembler import Assembler
    from ai_hats_observe import SidecarTracer
    from ..composition_seam import build_composition_payload, make_session_manager

    layout = resolve_project().layout
    project_dir = layout.root
    handoff_path = _build_handoff(layout)
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

    console.print(f"[cyan]→ Launching judge for reflect-all triage: {handoff_path}[/]")
    result = run_pipeline(
        REFLECT_ALL,
        SessionRunParams(
            project_dir=project_dir,
            role=MaterializedRole(
                name="judge",
                composition=build_composition_payload(
                    project_dir,
                    role_override="judge",
                    interactive=True,
                ),
            ),
            recording=SessionRecording(
                manager=make_session_manager(project_dir),
                tracer_factory=SidecarTracer,
            ),
            harness=Hitl(prompt=combined),
        ),
    )
    sys.exit(SessionOutcome.of(result).exit_code_or(1))


# ---- reflect hypothesis (HATS-513: 2-phase judge split) ----


@reflect.command("hypothesis")
@click.option(
    "--headless",
    is_flag=True,
    help="Phase 1 only — produce draft, exit. No HITL session.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Build pre-flight handoff but do not exec.",
)
def reflect_hypothesis_cmd(headless: bool, dry_run: bool):
    """Two-phase HYP closure + PROP triage.

    Phase 1 (`judge-auditor`, headless, read-only via SubAgentRunner)
    produces a draft with proposed verdicts and proposed CLI mutations.
    Phase 2 (`judge`, HITL via WrapRunner) consumes the draft, supervisor
    ack's mutations, judge executes them via CLI and writes the final
    report.

    With ``--headless``: only Phase 1 runs (CI / cron-safe, no
    state-mutating CLI calls possible by L0 contract).
    """
    from ..assembler import Assembler
    from ai_hats_observe import SidecarTracer
    from ..composition_seam import build_composition_payload, make_session_manager

    layout = resolve_project().layout
    project_dir = layout.root
    handoff_path = _build_handoff(layout)
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

    console.print("[cyan]→ Phase 1 — judge-auditor (headless audit)[/]")
    r1 = run_pipeline(
        REFLECT_HYPOTHESIS_PHASE1,
        SessionRunParams(
            project_dir=project_dir,
            role=MaterializedRole(
                name="judge-auditor",
                composition=build_composition_payload(
                    project_dir,
                    role_override="judge-auditor",
                ),
            ),
            recording=SessionRecording(
                manager=make_session_manager(project_dir),
                tracer_factory=SidecarTracer,
            ),
            harness=Automate(prompt=combined1),
        ),
    )

    # Fail closed: Phase 1 errored OR did not produce a usable draft.
    # `save_artifact` always emits `saved_path` (even on empty content),
    # so we additionally assert the draft file is non-empty — extract_marker
    # silently returns "" when BEGIN_JUDGE_DRAFT/END_JUDGE_DRAFT are missing
    # from the transcript, which would leave a zero-byte draft on disk and
    # mislead a Phase 2 session into discussing nothing.
    draft = ReportOutcome.of(r1).saved_path
    phase1_code = SessionOutcome.of(r1).exit_code_or(1)
    if phase1_code != 0 or draft is None:
        console.print("[red]✗[/] Phase 1 (judge-auditor) failed — Phase 2 aborted.")
        sys.exit(phase1_code or 1)

    draft_path = Path(draft)
    if not draft_path.exists() or draft_path.stat().st_size == 0:
        console.print(
            "[red]✗[/] Phase 1 produced an empty draft "
            f"({draft_path}) — markers missing from transcript. "
            "Phase 2 aborted."
        )
        sys.exit(1)
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
    preamble2 = _fill_inbox_digest(preamble2_path.read_text(), project_dir)
    combined2 = preamble2.replace("{draft_body}", draft_path.read_text())

    console.print("[cyan]→ Phase 2 — judge (HITL session with draft inlined)[/]")
    r2 = run_pipeline(
        REFLECT_HYPOTHESIS_PHASE2,
        SessionRunParams(
            project_dir=project_dir,
            role=MaterializedRole(
                name="judge",
                composition=build_composition_payload(
                    project_dir,
                    role_override="judge",
                    interactive=True,
                ),
            ),
            recording=SessionRecording(
                manager=make_session_manager(project_dir),
                tracer_factory=SidecarTracer,
            ),
            harness=Hitl(prompt=combined2),
        ),
    )
    sys.exit(SessionOutcome.of(r2).exit_code_or(1))


# ---- reflect role / reflect roles ----


@reflect.command("role")
@click.argument("name")
def reflect_role_cmd(name: str):
    """Audit a single role against the project context for coherence."""
    sys.exit(_run_role_audit(resolve_project().layout, name).exit_code_or(1))


@reflect.command("roles")
def reflect_roles_cmd():
    """Audit every role available to this project, one session per role."""
    from ..assembler import Assembler
    from ..models import ComponentType

    layout = resolve_project().layout
    project_dir = layout.root
    resolver = Assembler(project_dir).resolver
    names = resolver.list_components(ComponentType.ROLE)
    if not names:
        console.print("[yellow]No roles found in library.[/yellow]")
        sys.exit(1)

    console.print(f"[cyan]→ {len(names)} role(s) to audit: {', '.join(names)}[/]")
    worst_exit = 0
    for n in names:
        console.print(f"\n[bold cyan]── reflect role {n} ──[/]")
        ec = _run_role_audit(layout, n).exit_code_or(1)
        if ec != 0 and worst_exit == 0:
            worst_exit = ec
    sys.exit(worst_exit)


def _run_role_audit(layout: ProjectLayout, target_role: str) -> SessionOutcome:
    """Materialize the target role's layered breakdown and run reflect-role.

    The reviewer reads the composed files (and ./CLAUDE.md, user-rules)
    through Read/Glob tools during the interactive session, instead of
    receiving everything inlined in the prompt.
    """
    from ..assembler import Assembler
    from ai_hats_observe import SidecarTracer
    from ..composition_seam import build_composition_payload, make_session_manager

    project_dir = layout.root
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
        reported = "; ".join(str(e) for e in composition.errors)
        raise click.ClickException(f"Cannot compose role {target_role!r}: {reported}")

    preamble_path = assembler.resolver.resolve_injection("reflect-role")
    if preamble_path is None:
        raise click.ClickException(
            "built-in initial_injection 'reflect-role' not found in any "
            "library_path — ai-hats packaging is broken"
        )
    preamble_template = preamble_path.read_text()

    console.print(f"[cyan]→ Launching role-judge to audit: {target_role}[/]")
    result = run_pipeline(
        REFLECT_ROLE,
        SessionRunParams(
            project_dir=project_dir,
            role=MaterializedRole(
                name="role-judge",
                composition=build_composition_payload(
                    project_dir,
                    role_override="role-judge",
                    interactive=True,
                ),
            ),
            recording=SessionRecording(
                manager=make_session_manager(project_dir),
                tracer_factory=SidecarTracer,
            ),
            # No prompt on the harness: ``audit`` builds the first message, which
            # can only be written once the run's scratch dir exists.
            harness=Hitl(),
            audit=RoleAudit(
                target=target_role,
                materialize=lambda scratch: _materialize_target_composition(
                    scratch / "composed",
                    composition,
                    target_role,
                ),
                message_template=preamble_template,
            ),
        ),
    )
    outcome = SessionOutcome.of(result)
    if outcome.exit_code_or(1) == 0:
        # Not the file: role-judge names it with a timestamp of its own at Write time.
        console.print(
            f"[green]✓[/green] report under {layout.sessions.retros / 'role-coherence'}"
        )
    return outcome


def _materialize_target_composition(
    base_dir: Path,
    composition,
    target_role: str,
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
        (target_dir / "role-injection.md").write_text(composition.role_injection)
    if composition.overlay_injection:
        (target_dir / "overlay-injection.md").write_text(composition.overlay_injection)

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
            # HATS-706: read the body on demand from source_path. The composer
            # no longer eager-loads it into ``injection`` (reflect is its sole
            # consumer), so reading ``s.injection`` here would write an empty
            # file.
            skill_md = s.source_path / "SKILL.md"
            body = skill_md.read_text() if skill_md.exists() else ""
            (skills_dir / f"{s.name}.md").write_text(body)

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
        recent = [e.get("evidence") for e in h.validation_log[-3:] if e.get("evidence")]
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
    project_dir: Path,
    prompt_text: str,
) -> tuple[str, int]:
    """Invoke `reflect-issue` pipeline; return (intake_result_text, exit_code).

    Empty ``intake_result_text`` means the marker block was missing in the
    transcript. Caller treats that as a pipeline failure.
    """
    from ai_hats_observe import SidecarTracer
    from ..composition_seam import build_composition_payload, make_session_manager

    result = run_pipeline(
        REFLECT_ISSUE,
        SessionRunParams(
            project_dir=project_dir,
            role=MaterializedRole(
                name="hypothesis-intake",
                composition=build_composition_payload(
                    project_dir,
                    role_override="hypothesis-intake",
                ),
            ),
            recording=SessionRecording(
                manager=make_session_manager(project_dir),
                tracer_factory=SidecarTracer,
            ),
            harness=Automate(prompt=prompt_text, model=INTAKE_MODEL),
        ),
    )
    return IntakeOutcome.of(result).text, SessionOutcome.of(result).exit_code_or(1)


def _minimal_create_action(text: str):
    """Build a degraded CreateAction with only title+hypothesis populated.

    Used when the LLM round-trip fails AND there are no active HYPs (so we
    cannot deduplicate). The supervisor still gets a HYP file to edit; all
    schema-optional fields are left empty for a later pass.
    """
    from ..retro.intake import CreateAction, IntakeDraft

    title = text.strip().splitlines()[0][:60] or "supervisor observation"
    return CreateAction(
        action="create",
        draft=IntakeDraft(title=title, hypothesis=text.strip()),
    )


def _format_preview(action) -> str:
    """Pretty-print an IntakeResult for the interactive confirmation prompt."""
    import yaml as _yaml

    from ..retro.intake import CreateAction, MergeAction

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
    ws,
    action,
    *,
    text: str,
    session_id: str | None,
    task_id: str | None,
) -> str:
    """Materialize the intake decision via the rack workspace. Returns HYP id."""
    from datetime import datetime, timezone

    from ..retro.intake import CreateAction, MergeAction

    if isinstance(action, MergeAction):
        if not ws.exists(action.target_id):
            raise click.ClickException(
                f"intake returned merge target {action.target_id} "
                "but the card does not exist; refusing to fabricate"
            )
        entry = {
            "date": datetime.now(tz=timezone.utc).date().isoformat(),
            "verdict": "inconclusive",
            "evidence": action.evidence,
            "recommendation": "keep",
            "timestamp": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if session_id:
            entry["session_id"] = session_id
        append_verdict(ws, action.target_id, entry, caller_cwd=project_dir)
        return action.target_id

    if isinstance(action, CreateAction):
        d = action.draft
        return create_hypothesis(
            ws,
            title=d.title,
            hypothesis=d.hypothesis,
            source_task=task_id or SUPERVISOR_SOURCE_TASK,
            baseline=d.baseline,
            expected_outcome=list(d.expected_outcome),
            success_criterion=d.success_criterion,
            exit_criteria=(d.exit_criteria or None),
        )

    raise click.ClickException(  # pragma: no cover — defensive
        f"unexpected intake action type: {type(action).__name__}"
    )


def _spawn_intake_detached(
    text: str,
    session_id: str | None,
    task_id: str | None,
) -> tuple[int, Path]:
    """Re-invoke ``ai-hats reflect issue`` as a detached process.

    Returns ``(pid, log_path)``.

    The child runs in foreground default mode (no preview, no --bg) so it
    writes the intake when the pipeline returns. Output is appended to a
    timestamped log under ``<ai_hats_dir>/sessions/runs/reflect-issue/``.
    """
    import subprocess
    from datetime import datetime, timezone

    layout = resolve_project().layout
    project_dir = layout.root
    log_dir = layout.sessions.runs / REFLECT_ISSUE.name
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = log_dir / f"{ts}-bg.log"

    cmd = [
        sys.executable,
        "-c",
        "from ai_hats.cli import main_entry; main_entry()",
        "reflect",
        "issue",
        text,
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
    "--preview",
    "-n",
    "preview_mode",
    is_flag=True,
    help="Show the intake draft and prompt before writing.",
)
@click.option(
    "--bg",
    "--background",
    "background",
    is_flag=True,
    help="Run detached; return immediately. Output goes to .gitlog/reflect-issue/.",
)
@click.option(
    "--session",
    "session_id",
    default=None,
    help="Source session id (YYYYMMDD-HHMMSS-N) — recorded on merge.",
)
@click.option(
    "--task",
    "task_id",
    default=None,
    help="Originating task id; defaults to 'supervisor-observation'.",
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
    from ..composition_seam import MissingProviderError
    from ..retro.intake import IntakeParseError, parse_intake_yaml

    if background and preview_mode:
        raise click.ClickException("--bg and --preview are mutually exclusive")

    if background:
        pid, log_path = _spawn_intake_detached(text, session_id, task_id)
        console.print(f"[dim]reflect issue spawned (pid={pid}, bg) → {log_path}[/dim]")
        return

    project_dir = resolve_project().layout.root
    # reflect issue creates an HYP; mount its backlog on a project that never had one.
    ensure_backlog(project_dir, "hypotheses")
    ws = rack_workspace(project_dir)
    active = active_hypotheses(ws)
    prompt_text = _build_intake_prompt(text, active)

    action = None
    degraded = False
    try:
        intake_text, exit_code = _run_intake_pipeline(project_dir, prompt_text)
        if exit_code != 0:
            raise RuntimeError(f"reflect-issue pipeline exited non-zero ({exit_code})")
        if not intake_text:
            raise RuntimeError(
                "reflect-issue pipeline did not emit BEGIN_INTAKE_RESULT/END_INTAKE_RESULT block"
            )
        action = parse_intake_yaml(intake_text)
    except MissingProviderError:
        raise
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
        project_dir,
        ws,
        action,
        text=text,
        session_id=session_id,
        task_id=task_id,
    )
    from ..retro.intake import MergeAction

    if isinstance(action, MergeAction):
        console.print(f"[green]✓[/green] merged into {hyp_id} (validation_log +1)")
    else:
        console.print(f"[green]✓[/green] created {hyp_id} (status=active)")


# ---- reflect commit ----


@reflect.command("commit")
@click.option(
    "--accept",
    multiple=True,
    help="PROP-NNN to mark accepted (repeatable)",
)
@click.option(
    "--reject",
    multiple=True,
    help="PROP-NNN to mark rejected (repeatable)",
)
@click.option(
    "--defer",
    multiple=True,
    help="PROP-NNN to mark deferred (repeatable)",
)
@click.option(
    "--duplicate",
    multiple=True,
    help="PROP-NNN to mark duplicate (repeatable)",
)
def reflect_commit_cmd(accept, reject, defer, duplicate):
    """Bulk-update proposal statuses (called at end of interactive chat)."""
    project_dir = resolve_project().layout.root
    ws = rack_workspace(project_dir)
    changes = 0
    for pid, to_state in (
        *((p, "accepted") for p in accept),
        *((p, "rejected") for p in reject),
        *((p, "deferred") for p in defer),
        *((p, "duplicate") for p in duplicate),
    ):
        set_proposal_status(ws, pid, to_state, caller_cwd=project_dir)
        console.print(f"  {pid} → {to_state}")
        changes += 1
    console.print(f"[green]✓[/green] reflect commit: {changes} change(s)")


# ---- pre-flight handoff (used by `reflect all`) ----


def _handoff_dir(retros: Path) -> Path:
    return retros / REFLECT_ALL.name


def _build_handoff(layout: ProjectLayout) -> Path:
    """Collect active HYP + open PROP into a single markdown handoff file."""
    ws = rack_workspace(layout.root)
    active = active_hypotheses(ws)
    open_props = open_proposals(ws)

    out_dir = _handoff_dir(layout.sessions.retros)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    path = out_dir / f"{ts}-handoff.md"

    parts: list[str] = []
    parts.append(f"# reflect-all handoff — {ts}\n")
    parts.append(f"Active hypotheses: {len(active)} · Open proposals: {len(open_props)}\n")

    parts.append("## Active hypotheses\n")
    if active:
        for h in active:
            parts.append(
                f"### {h.id} — {h.title}\n"
                f"- success_criterion: {h.success_criterion!r}\n"
                f"- observation_window: {h.observation_window!r}\n"
                f"- validation_log entries: {len(h.validation_log)}\n"
            )
            # HATS-534 — surface verification_protocol as a literal block scalar
            # so multi-line protocols stay verbatim for judge consumption.
            vp = h.verification_protocol
            if vp:
                indented = "\n".join(f"    {line}" for line in str(vp).splitlines())
                parts.append(f"- verification_protocol: |\n{indented}")
            recent = h.validation_log[-3:]
            if recent:
                parts.append("  Recent verdicts:")
                for e in recent:
                    parts.append(f"  - {e.get('date')} · {e.get('verdict')} · {e.get('evidence')}")
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
                f"- related_hypotheses: {list(p.related_hypotheses)}\n"
            )
            if p.failed_session_id:
                parts.append(f"- **meta-proposal** failed_session_id: {p.failed_session_id}")
            parts.append("")
    else:
        parts.append("(inbox empty)\n")

    path.write_text("\n".join(parts))
    return path


# ---- open-PROP digest (Phase 2 preamble; HATS-1385) ----

#: What the runtime safety net stamps on an auto-filed PROP — see
#: `cli/reflect_session_main.py` TARGET_* and `_build_meta_proposal_body`.
_AUTO_TARGETS = frozenset({"harness-incident", "session-reviewer"})
_AUTO_TITLES = ("harness incident: ", "session-reviewer incomplete: ")

_DIGEST_TOP = 5


def _is_auto_filed(prop) -> bool:
    """Target AND title AND no vote — each clause has a measured victim without
    the others (HATS-1385): a card *about* the harness wears the harness target
    (PROP-107, 5 votes), and `--failed-session-id` marks authorship, not origin."""
    return prop.target in _AUTO_TARGETS and prop.title.startswith(_AUTO_TITLES) and not prop.votes


def _prop_number(prop_id: str) -> int:
    """`PROP-107` → 107. Ids are monotonic, so id order is filing order — the
    age axis needs no date field on the card."""
    tail = prop_id.rpartition("-")[2]
    return int(tail) if tail.isdigit() else 0


def _build_inbox_digest(project_dir: Path) -> str:
    """Compact open-PROP inventory for the Phase 2 preamble.

    A digest, not the handoff's dump: that section measured ~126K chars on a
    147-card inbox, and the judge handed it still missed the inbox (HATS-1323).
    Ranked on two axes — votes AND age — because vote counts predating the
    HATS-1397 reviewer fix are systematically depressed.
    """
    props = open_proposals(rack_workspace(project_dir))
    if not props:
        return "## PROP inbox\n\n(inbox empty — 0 open proposals)\n"

    auto = [p for p in props if _is_auto_filed(p)]
    rest = [p for p in props if not _is_auto_filed(p)]
    lines = [
        f"## PROP inbox — {len(props)} open",
        "",
        f"- auto-filed, unvoted: {len(auto)} — ONE batch decision under a shared "
        "criterion (**review-proposal** Step 3b), not that many judgements",
        f"- everything else: {len(rest)} — the half that needs you",
    ]
    if rest:
        lines += ["", f"Leaders by votes (top {_DIGEST_TOP}):"]
        by_votes = sorted(rest, key=lambda p: (-len(p.votes), _prop_number(p.id)))
        lines += [f"- {p.id} ({len(p.votes)} votes) — {p.title}" for p in by_votes[:_DIGEST_TOP]]
        lines += [
            "",
            f"Longest open (top {_DIGEST_TOP}) — the second axis: votes cast before "
            "the HATS-1397 reviewer fix run low, so age carries what votes cannot:",
        ]
        by_age = sorted(rest, key=lambda p: _prop_number(p.id))
        lines += [f"- {p.id} ({len(p.votes)} votes) — {p.title}" for p in by_age[:_DIGEST_TOP]]
    lines += [
        "",
        "Full inventory: `rack ls --backlog proposal --state open --all` "
        "(**judge-protocol** Step 0).",
    ]
    return "\n".join(lines) + "\n"


def _fill_inbox_digest(preamble: str, project_dir: Path) -> str:
    """Substitute `{inbox_digest}`; append it when an overridden injection has
    dropped the placeholder — a judge silently launched without the inbox is the
    HATS-1323 failure itself, so this says so rather than shipping the gap."""
    digest = _build_inbox_digest(project_dir)
    if "{inbox_digest}" in preamble:
        return preamble.replace("{inbox_digest}", digest)
    console.print(
        "[yellow]![/] reflect-hypothesis-interactive carries no {inbox_digest} "
        "placeholder — appending the PROP inbox digest at the end."
    )
    return f"{preamble}\n\n---\n\n{digest}"
