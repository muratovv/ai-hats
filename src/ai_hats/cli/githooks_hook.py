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
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    from ..assembler import Assembler
    from ..githooks_resolve import resolve_git_gates
    from ..githooks_run import run_chain
    from ..hooks_manager import GITHOOKS_BYPASS_JOURNAL
    from ..materialize import compose_for_role
    from ..paths import builtin_library_hooks

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
    args = parser.parse_args(argv)

    project_dir: Path = args.project_dir
    assembler = Assembler(project_dir)
    cfg = assembler.project_config
    role = cfg.active_role or cfg.default_role

    gates: list[Path] = []
    journal: Path | None = None
    if role:
        resolution = resolve_git_gates(compose_for_role(assembler, role), args.event)
        for refusal in resolution.refusals:
            print(f"ai-hats: git gate refused — {refusal}", file=sys.stderr)
        gates = [g.path for g in resolution.gates]

        hooks_root = builtin_library_hooks(project_dir)
        candidate = None if hooks_root is None else hooks_root / GITHOOKS_BYPASS_JOURNAL
        if candidate is not None and candidate.is_file():
            journal = candidate
        else:
            # Every hatch branch sources this; without it the gates still run
            # but stop recording bypasses (HATS-1407).
            print(
                "ai-hats: bypass journal not found — gate bypasses will NOT be recorded",
                file=sys.stderr,
            )

    return run_chain(
        event=args.event,
        project_dir=project_dir,
        githooks_dir=args.githooks_dir,
        gates=gates,
        journal=journal,
        argv=args.hook_args,
    )


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    sys.exit(main())
