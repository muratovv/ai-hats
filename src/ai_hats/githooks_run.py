"""Run one git event's hook chain (HATS-1337).

Everything the installed `.githooks/<event>` stub used to do in bash lives here,
so that file can stay frozen: it only finds an interpreter and delegates. Order,
stdin replay, drop-ins and the pre-takeover chain are all revisable from the
package, with nothing to re-install in projects.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping, MutableMapping
from pathlib import Path

from ai_hats_core import scrubbed_git_env
from ai_hats_core.deadline import Deadline

from ai_hats_core.layout import pin_is_foreign
from .env import (
    AI_HATS_PROJECT_DIR_ENV,
    ENV_AI_HATS_DIR,
    ENV_AI_HATS_VENV,
    ENV_GIT_GATE_BROKEN_ACK,
    ENV_HOOK_EVENT,
    ENV_GIT_HOOK_TIMEOUT_S,
    GIT_HOOK_TIMEOUT,
    read_budget,
)
from .hook_exec import HookOutcomeKind, HookRun, run_hook
from .session_identity import IDENTITY_ENV_KEYS, drop_identity

#: Git events that deliver a protocol on stdin every hook must see.
STDIN_PROTOCOL_EVENTS = frozenset(
    {
        "pre-push",
        "pre-receive",
        "post-receive",
        "post-rewrite",
        "proc-receive",
        "reference-transaction",
    }
)

PREVIOUS_HOOKS_PATH_KEY = "ai-hats.previousHooksPath"

#: Fully-qualified point names this channel mints, e.g. ``git:pre-commit``.
GIT_POINT_PREFIX = "git:"

#: Generous on purpose: a `pre-commit` that runs a test suite is not a hang, and
#: the bound exists for the caller who cannot press Ctrl-C — CI, cron, an agent
#: session. Overridable per project.
GIT_HOOK_TIMEOUT_S: float = GIT_HOOK_TIMEOUT.default
GIT_HOOK_TIMEOUT_ENV = ENV_GIT_HOOK_TIMEOUT_S

#: Opens a materialization refusal. Named to match the established flag shape
#: (`ACK_FLAG_RE` in tests/e2e/_helpers/hook_chain.py) so the deny-names-its-hatch
#: invariant recognises it.
GATE_BROKEN_ACK_ENV = ENV_GIT_GATE_BROKEN_ACK

#: What a refusal with no exit status of its own returns to git.
GATE_BROKEN_EXIT = 1

#: Outcomes meaning "ai-hats could not DELIVER a runnable gate" (ADR-0020 D2
#: `CORRUPT`, plus a budget that expired before the script started). The gate
#: never formed a verdict, so there is no author's decision to defer to — which
#: is exactly what separates these from a gate that ran and exited non-zero.
_MATERIALIZATION_KINDS = frozenset(
    {
        HookOutcomeKind.SCRIPT_MISSING,
        HookOutcomeKind.NOT_EXECUTABLE,
        HookOutcomeKind.COMMAND_NOT_FOUND,
        HookOutcomeKind.EXEC_FAILED,
        HookOutcomeKind.LOG_UNUSABLE,
        HookOutcomeKind.NO_TIME_LEFT,
    }
)


def resolve_git_hook_timeout() -> float:
    """The effective per-script budget. A typo must not disable the bound, so a
    non-numeric or non-positive override falls back to the default."""
    return read_budget(GIT_HOOK_TIMEOUT)


def _log_dir(project_dir: Path) -> Path | None:
    """Where a gate's full output is kept, beside the bypass journal.

    ``None`` when git will not say where its common dir is: the run then still
    tees to the terminal and still carries a reason, so the loss is the
    postmortem file alone — never the verdict.
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            check=False,
            env=scrubbed_git_env(),
        )
    except OSError as exc:
        print(f"ai-hats: no hook log dir ({exc}) — output stays on screen only", file=sys.stderr)
        return None
    if proc.returncode != 0:
        print("ai-hats: no hook log dir — output stays on screen only", file=sys.stderr)
        return None
    common = Path(proc.stdout.strip())
    if not common.is_absolute():
        common = project_dir / common
    return common / "ai-hats" / "hook-logs"


def _dropins(githooks_dir: Path, event: str) -> list[Path]:
    """Executables a human dropped into `<event>.d/` — read, never written."""
    event_d = githooks_dir / f"{event}.d"
    if not event_d.is_dir():
        return []
    return sorted(p for p in event_d.iterdir() if p.is_file() and os.access(p, os.X_OK))


def _previous_hook(project_dir: Path, githooks_dir: Path, event: str) -> Path | None:
    """The repo's own hook manager's hook for this event, if it is not us.

    ai-hats owns `core.hooksPath`, but simple-git-hooks / husky keep writing to
    the location they owned before the takeover, and they regenerate on their own
    schedule — so this resolves live rather than from a snapshot (HATS-999).
    """
    try:
        proc = subprocess.run(
            ["git", "config", "--get", PREVIOUS_HOOKS_PATH_KEY],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            check=False,
            # A hook runs with git's own GIT_DIR exported; unscrubbed it would
            # retarget the lookup away from project_dir.
            env=scrubbed_git_env(),
        )
    except OSError as exc:
        print(f"ai-hats: cannot read {PREVIOUS_HOOKS_PATH_KEY}: {exc}", file=sys.stderr)
        return None

    previous = proc.stdout.strip() if proc.returncode == 0 else ""
    base = Path(previous or ".git/hooks")
    if not base.is_absolute():
        base = project_dir / base
    candidate = base / event
    if not (candidate.is_file() and os.access(candidate, os.X_OK)):
        return None
    # Recursion guard: never chain back into our own hooks dir.
    if base.resolve() == githooks_dir.resolve():
        return None
    return candidate


#: `hook` for a skip nobody's script produced — the dispatcher decided it.
DISPATCHER = "githooks-dispatcher"


def record_fail_open(
    journal: Path | None,
    *,
    reason: str,
    event: str,
    hook: str = DISPATCHER,
    project_dir: Path | None = None,
) -> None:
    """Say — on stderr AND in the bypass journal — that a gate was skipped.

    ADR-0020 D2 forbids passing a gate **silently**, not passing it: that row is
    the machine form of ADR-0019 D4's anti-disarm rule. Refusing outright here
    would wedge a human commit (D3) and push them to ``--no-verify``, which
    disarms the WHOLE chain — so the skip is recorded instead, where
    ``pre-push-bypass-report.sh`` surfaces it at push time.

    The writer is a SIBLING of the sourced shell wrapper — the contract
    ``bypass_journal.sh`` states — and is spawned rather than imported: the
    library may be a user tree outside the installed package.
    """  # comment-length: allow — why a skip is recorded and not refused is the point
    print(f"ai-hats: git gate SKIPPED (fail-open) — {reason}", file=sys.stderr)
    writer = None if journal is None else journal.with_name("bypass_journal.py")
    if writer is None or not writer.is_file():
        print(f"ai-hats: fail-open NOT RECORDED ({reason}) — no journal writer", file=sys.stderr)
        return
    try:
        # stderr is NOT captured: the writer's own "NOT RECORDED …" diagnostics
        # are the only signal that the record was lost, so they must reach the human.
        proc = subprocess.run(  # noqa: S603 — fixed argv; writer from our own library
            [
                sys.executable,
                str(writer),
                "record",
                "--kind",
                "fail_open",
                "--reason",
                reason,
                "--hook",
                hook,
            ],
            env={**os.environ, ENV_HOOK_EVENT: event},
            # The writer resolves `--git-common-dir` from where it stands, so the
            # project must be TOLD, not inferred: inferring put a test's synthetic
            # skips in the maintainer's own audit journal.
            cwd=None if project_dir is None else str(project_dir),
            check=False,
        )
    except OSError as exc:
        print(f"ai-hats: fail-open NOT RECORDED ({reason}) — {exc}", file=sys.stderr)
        return
    if proc.returncode != 0:
        print(
            f"ai-hats: fail-open NOT RECORDED ({reason}) — writer exit {proc.returncode}",
            file=sys.stderr,
        )


def foreign_pin_drops(env: Mapping[str, str], project_dir: Path) -> list[str]:
    """Names that must not reach a child because they travel with a foreign pin.

    The decision of :func:`_drop_foreign_pin` without the mutation: `run_chain`
    hands the primitive a list of removals rather than a cleaned copy, and the
    two must never diverge on what "foreign" means.
    """
    pin = env.get(AI_HATS_PROJECT_DIR_ENV)
    if not pin_is_foreign(pin, project_dir):
        return []
    return [
        *(name for name in (ENV_AI_HATS_VENV, ENV_AI_HATS_DIR) if name in env),
        *(name for name in IDENTITY_ENV_KEYS if name in env),
    ]


def _drop_foreign_pin(env: MutableMapping[str, str], project_dir: Path) -> None:
    """Strip a session pin naming another project before the children see it.

    ADR-0025 D3. The current stub unsets ``AI_HATS_VENV``/``AI_HATS_DIR`` but not
    the pin itself, so the usual path here is the silent re-pin: the children are
    told this project rather than the one the pin names. The warn branch means
    the INSTALLED stub predates the guard — the delivery window between two
    ``self …`` runs — so that window announces itself.
    """  # comment-length: allow — the branch only fires in a window worth naming
    pin = env.get(AI_HATS_PROJECT_DIR_ENV)
    if not pin_is_foreign(pin, project_dir):
        return
    dropped = [name for name in (ENV_AI_HATS_VENV, ENV_AI_HATS_DIR) if env.pop(name, None)]
    # The identity names the OTHER project, so re-pinning around it would leave a
    # gate composing under that session's role — the same leak, identity axis.
    drop_identity(env)
    # Re-pin rather than leave the lie: a gate reading it must get this project.
    env[AI_HATS_PROJECT_DIR_ENV] = str(project_dir)
    if dropped:
        print(
            f"ai-hats: dropped {', '.join(dropped)} pinned to {pin} — foreign to "
            f"{project_dir}. The installed git-hook stub predates this guard; "
            f"run `ai-hats self update` to refresh it.",
            file=sys.stderr,
        )


def run_chain(
    *,
    event: str,
    project_dir: Path,
    githooks_dir: Path,
    gates: list[Path],
    journal: Path | None,
    argv: list[str],
) -> int:
    """Run gates, then the project's drop-ins, then its pre-takeover hook.

    Returns the first non-zero exit — git's contract for a refusing hook.

    Every script goes through ``hook_exec.run_hook`` (ADR-0020 D2), so each is
    bounded, logged and classified. What the class buys is the split this channel
    could not make before: a gate that RAN and failed is the script author's
    business and propagates untouched, while a gate that could not be DELIVERED
    is ai-hats' own failure and refuses, naming the flag that opens it.
    """
    scripts = [*gates, *_dropins(githooks_dir, event)]
    chained = _previous_hook(project_dir, githooks_dir, event)
    if chained is not None:
        scripts.append(chained)
    if not scripts:
        return 0

    # A gate's own $0 is its library path, so it cannot recover the event from it.
    extra = {ENV_HOOK_EVENT: event}
    if journal is not None:
        # The gates' relative fallback is only correct inside the builtin
        # library; the resolved path is what makes it work everywhere.
        extra["AI_HATS_BYPASS_JOURNAL"] = str(journal)
    drops = foreign_pin_drops(os.environ, project_dir)

    # Read the ref protocol ONCE and replay it into each script — one shared
    # stdin would let the first consumer drain it. Never read on a
    # stdin-less event: an open pipe on fd 0 would block forever.
    replay = event in STDIN_PROTOCOL_EVENTS
    payload = sys.stdin.buffer.read() if replay else None
    budget = resolve_git_hook_timeout()
    logs = _log_dir(project_dir)

    for script in scripts:
        run = run_hook(
            script,
            point=f"{GIT_POINT_PREFIX}{event}",
            budget=budget,
            # No lock governs a git hook, and each script gets its own budget:
            # one ceiling over the chain would feed the last gates the leftovers.
            deadline=Deadline.without_lock(budget, why=f"git {event}"),
            project_dir=project_dir,
            # Where git called us, NOT project_dir: `core.hooksPath` is absolute,
            # so a commit inside a linked worktree still dispatches from the main
            # checkout, and a gate that roots itself with `rev-parse` would then
            # inspect the wrong tree and pass (pinned by the worktree e2e).
            cwd=Path.cwd(),
            argv=argv,
            stdin_payload=payload,
            # The human ran `git commit` and is watching; capture alone would go
            # silent until the gate finished.
            tee=True,
            extra_env=extra,
            drop_env=drops,
            log_path=None if logs is None else logs / f"{event}-{script.name}.log",
        )
        if run.ok:
            continue
        skipped = _skip_reason(run, script)
        if skipped is not None:
            record_fail_open(
                journal, reason=skipped, event=event, hook=script.name, project_dir=project_dir
            )
            continue
        label = "chained project hook" if script == chained else "hook"
        print(f"ai-hats: {label} '{script.name}' failed — {run.reason}", file=sys.stderr)
        hatch = _hatch_line(run)
        if hatch:
            print(f"ai-hats: {hatch}", file=sys.stderr)
        return run.exit_code if run.exit_code else GATE_BROKEN_EXIT
    return 0


def _hatch_line(run: HookRun) -> str:
    """The way out this refusal leaves open, or "" when the gate simply refused.

    A gate's own verdict needs no hatch — arguing with it is between the author
    and whoever it stopped. Everything ai-hats itself imposed owes the human a
    named exit, or it just manufactures `--no-verify` (ADR-0020 D3, HATS-1828).
    """
    if run.kind is HookOutcomeKind.TIMED_OUT:
        return f"the gate hit its budget — raise {GIT_HOOK_TIMEOUT_ENV} if it needs longer"
    if run.kind in _MATERIALIZATION_KINDS:
        return f"this gate could not run — set {GATE_BROKEN_ACK_ENV}=1 to commit past it"
    return ""


def _skip_reason(run: HookRun, script: Path) -> str | None:
    """Why this outcome is skipped rather than refused, or ``None`` to refuse.

    The hatch is read HERE and not at the top, so the deny below always states a
    name that actually works — the deny-names-its-hatch invariant is worth
    nothing if the flag it names is inert (HATS-1253 P4).
    """
    if run.kind not in _MATERIALIZATION_KINDS:
        return None
    if os.environ.get(GATE_BROKEN_ACK_ENV):
        return f"{GATE_BROKEN_ACK_ENV} set — '{script.name}' SKIPPED: {run.reason}"
    return None
