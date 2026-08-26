"""The entry point every installed git hook delegates to (HATS-1337).

**This module path is a frozen contract**: `.githooks/<event>` stubs in the field
name it literally and are never re-installed, so renaming it silently disables
every git gate in every project composed before the rename (pinned by
`test_githooks_stub.py`). A module and never a subcommand — an unregistered
subcommand reads as a bare prompt and launches a provider session
(`_PassthroughGroup`, HATS-1202); a module path either imports or raises, and
raising is the fail-open path.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    from ..assembler import Assembler
    from ..githooks_resolve import resolve_git_gates
    from ..githooks_run import (
        GATE_BROKEN_ACK_ENV,
        GATE_BROKEN_EXIT,
        _drop_foreign_pin,
        record_fail_open,
        run_chain,
    )
    from ..hooks_manager import GITHOOKS_BYPASS_JOURNAL
    from ..materialize import compose_for_role
    from ..paths import builtin_library_hooks
    from ..session_identity import IdentityFault, SessionIdentity, SessionIdentityError

    parser = argparse.ArgumentParser(prog="ai_hats.cli.githooks_hook")
    parser.add_argument("event")
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument(
        "--githooks-dir",
        required=True,
        type=Path,
        help="Where the stub lives. Passed rather than derived: core.hooksPath "
        "may name any directory, and only the stub knows which one ran.",
    )
    parser.add_argument("hook_args", nargs="*", help="arguments git passed to the hook")

    raw = list(sys.argv[1:] if argv is None else argv)
    # The stub's `--` guards a hook argument starting with `-`; argparse honours
    # that separator only on 3.13+, so the split is ours to make (HATS-1519).
    passthrough: list[str] | None = None
    if "--" in raw:
        cut = raw.index("--")
        raw, passthrough = raw[:cut], raw[cut + 1 :]
    try:
        args = parser.parse_args(raw)
    except SystemExit as exc:
        # Scoped to the parse alone: past this point a non-zero code is a gate's
        # verdict, and swallowing that would disable the gates silently.
        code = exc.code if isinstance(exc.code, int) else 0
        if not code:
            return 0
        # Nothing is known here — not the project, so not the journal either —
        # so this is the one refusal that cannot be recorded, and says so.
        if os.environ.get(GATE_BROKEN_ACK_ENV):
            print(
                f"ai-hats: {GATE_BROKEN_ACK_ENV} set — dispatcher arguments unparsed, all git "
                "gates SKIPPED and NOT RECORDED (no project resolved); run 'ai-hats self update'",
                file=sys.stderr,
            )
            return 0
        print(
            f"ai-hats: hook dispatcher cannot parse its own arguments (exit {code}) — the "
            "installed stub and this ai-hats disagree. Run 'ai-hats self update'; to commit "
            f"first, set {GATE_BROKEN_ACK_ENV}=1",
            file=sys.stderr,
        )
        return GATE_BROKEN_EXIT

    project_dir: Path = args.project_dir

    # Resolved FIRST, before anything that can refuse (HATS-1828): every branch
    # below either records a skip or refuses, and one resolved after them could
    # not record its own. HATS-1597 already wanted it before composition.
    journal: Path | None = None
    hooks_root = builtin_library_hooks(project_dir)
    candidate = None if hooks_root is None else hooks_root / GITHOOKS_BYPASS_JOURNAL
    if candidate is not None and candidate.is_file():
        journal = candidate
    else:
        # Every hatch branch sources this; without it the gates still run
        # but stop recording bypasses (HATS-1407).
        print(
            f"ai-hats: bypass journal not found — gate bypasses, including "
            f"{GATE_BROKEN_ACK_ENV}, will NOT be recorded",
            file=sys.stderr,
        )

    def refuse(reason: str) -> int:
        """ai-hats could not deliver the gates: refuse, or take the stated hatch.

        The hatch is read HERE so the message can only ever name a flag that
        works — a deny leaving the human nowhere to go just produces
        `--no-verify`, which disarms the whole chain (ADR-0020 D3, HATS-1828).
        """
        if os.environ.get(GATE_BROKEN_ACK_ENV):
            record_fail_open(
                journal,
                reason=f"{GATE_BROKEN_ACK_ENV} set — {reason}",
                event=args.event,
                project_dir=project_dir,
            )
            return 0
        print(
            f"ai-hats: git gates REFUSED — {reason}\n"
            f"ai-hats: fix it, or set {GATE_BROKEN_ACK_ENV}=1 to commit past this gate",
            file=sys.stderr,
        )
        return GATE_BROKEN_EXIT

    # A cleaned COPY, never os.environ: a foreign pin makes the whole envelope
    # foreign, and mutating the process env would leak into every later caller.
    scoped_env = dict(os.environ)
    _drop_foreign_pin(scoped_env, project_dir)
    assembler = Assembler(project_dir)
    # HATS-1594: in a session the role is what THAT session composed; the config
    # is the answer only outside one. Reading it unconditionally ran maintainer's
    # git gates inside a judge session, which never declared them.
    identity = None
    try:
        identity = SessionIdentity.from_env(scoped_env)
    except SessionIdentityError as exc:
        # HATS-1643: one condition, two reactions. Nothing is torn when the
        # session simply predates the envelope, so the configured role is a
        # sound answer and the gates still run — degraded, and recorded as such.
        # A torn or version-drifted envelope is a trust failure, and this type's
        # own docstring says never a skip.
        if exc.fault is not IdentityFault.TOO_OLD:
            return refuse(f"session identity cannot be trusted: {exc}")
        record_fail_open(
            journal,
            reason=f"session cannot name itself, gates run under the configured role: {exc}",
            event=args.event,
            project_dir=project_dir,
        )
    if identity is not None:
        role = identity.role
    else:
        cfg = assembler.project_config
        role = cfg.active_role or cfg.default_role

    gates: list[Path] = []
    if role:
        try:
            resolution = resolve_git_gates(compose_for_role(assembler, role), args.event)
        except Exception as exc:  # noqa: BLE001 — a hook must never raise at a human
            # A composition refusal (removed script, unknown point name) renders
            # friendly only in the click layer, which a git hook never enters —
            # so it arrived here as a traceback on `git commit`.
            return refuse(f"composition failed: {type(exc).__name__}: {exc}")
        if resolution.refusals:
            # A declared gate that cannot run is the anti-disarm case D2 names:
            # deleting or chmod-ing a script would otherwise silently retire it.
            return refuse("; ".join(resolution.refusals))
        gates = [g.path for g in resolution.gates]

    return run_chain(
        event=args.event,
        project_dir=project_dir,
        githooks_dir=args.githooks_dir,
        gates=gates,
        journal=journal,
        argv=args.hook_args if passthrough is None else passthrough,
    )


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    sys.exit(main())
