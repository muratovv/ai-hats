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
from collections.abc import MutableMapping
from pathlib import Path

from ai_hats_core import scrubbed_git_env

from .env import AI_HATS_PROJECT_DIR_ENV, ENV_AI_HATS_DIR, ENV_AI_HATS_VENV
from .session_identity import drop_identity

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
            # retarget the lookup away from project_dir (HATS-887).
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
            env={**os.environ, "AI_HATS_HOOK_EVENT": event},
            # The writer resolves `--git-common-dir` from where it stands, so the
            # project must be TOLD, not inferred: inferring put a test's synthetic
            # skips in the maintainer's own audit journal (HATS-1686).
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


def _drop_foreign_pin(env: MutableMapping[str, str], project_dir: Path) -> None:
    """Strip a session pin naming another project before the children see it.

    ADR-0025 D3. The current stub unsets ``AI_HATS_VENV``/``AI_HATS_DIR`` but not
    the pin itself, so the usual path here is the silent re-pin: the children are
    told this project rather than the one the pin names. The warn branch means
    the INSTALLED stub predates the guard — the delivery window between two
    ``self …`` runs — so that window announces itself.
    """  # comment-length: allow — the branch only fires in a window worth naming
    pin = env.get(AI_HATS_PROJECT_DIR_ENV)
    if not pin or Path(pin).expanduser().resolve() == project_dir.resolve():
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
    """
    scripts = [*gates, *_dropins(githooks_dir, event)]
    chained = _previous_hook(project_dir, githooks_dir, event)
    if chained is not None:
        scripts.append(chained)
    if not scripts:
        return 0

    env = dict(os.environ)
    _drop_foreign_pin(env, project_dir)
    # A gate's own $0 is its library path, so it cannot recover the event from it.
    env["AI_HATS_HOOK_EVENT"] = event
    if journal is not None:
        # The gates' relative fallback is only correct inside the builtin
        # library; the resolved path is what makes it work everywhere.
        env["AI_HATS_BYPASS_JOURNAL"] = str(journal)

    # Read the ref protocol ONCE and replay it into each script — one shared
    # stdin would let the first consumer drain it (HATS-654). Never read on a
    # stdin-less event: an open pipe on fd 0 would block forever.
    replay = event in STDIN_PROTOCOL_EVENTS
    payload = sys.stdin.buffer.read() if replay else None

    for script in scripts:
        try:
            proc = subprocess.run([str(script), *argv], env=env, input=payload, check=False)
        except OSError as exc:
            # Drop-ins and the chained hook never pass through resolve_git_gates,
            # and a mode can change between its check and this execve — so the
            # exec itself must degrade too, never raise at a human's commit.
            record_fail_open(
                journal,
                reason=f"cannot execute '{script.name}': {exc}",
                event=event,
                hook=script.name,
                project_dir=project_dir,
            )
            continue
        if proc.returncode != 0:
            label = "chained project hook" if script == chained else "hook"
            print(
                f"ai-hats: {label} '{script.name}' failed (exit {proc.returncode})",
                file=sys.stderr,
            )
            return proc.returncode
    return 0
