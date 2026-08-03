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
from pathlib import Path

from ai_hats_core import scrubbed_git_env

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
        proc = subprocess.run([str(script), *argv], env=env, input=payload, check=False)
        if proc.returncode != 0:
            label = "chained project hook" if script == chained else "hook"
            print(
                f"ai-hats: {label} '{script.name}' failed (exit {proc.returncode})",
                file=sys.stderr,
            )
            return proc.returncode
    return 0
