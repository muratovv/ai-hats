"""Hidden entry-point: ``python -m ai_hats._bump_internal``.

Used by ``ai-hats self update`` (see :mod:`ai_hats.cli.maintenance`)
to run the bump pipeline in a **fresh interpreter** so the freshly
installed code (migrations, healer, assembler) is loaded — running
``bump`` in-process after ``uv pip install --reinstall`` would
silently keep the pre-update code in memory (HATS-400).

**NOT a user-facing CLI.** Deliberately not exposed in
``ai-hats --help`` / ``ai-hats --tree`` / ``[project.scripts]`` —
HATS-470 removed ``ai-hats self bump`` from the click surface; this
module is the stable-but-private subprocess hook that ``self update``
shells into. End-users should run ``ai-hats self update`` instead.

Argument surface kept minimal — flags mirror the now-deleted
``self bump`` options:

  ``--migrate-force``    bypass v0.6 → v0.7 user-edit refusal
  ``--check-branches``   warn if local branches modify deletion-set paths

Exit codes mirror :func:`ai_hats.cli.assembly.do_bump`:

  0  success
  1  AssemblyError (user edits detected, refusal raised)
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    """Entry point — parses args, delegates to ``do_bump``, returns exit code.

    ``argv`` defaults to ``sys.argv[1:]``; tests pass an explicit list so
    pytest's own argv doesn't leak in.
    """
    # Avoid argparse / click overhead; flag-set is tiny and stable.
    args = sys.argv[1:] if argv is None else argv
    migrate_force = "--migrate-force" in args
    check_branches = "--check-branches" in args

    # Reject unknown flags so subprocess callers can't silently no-op a typo.
    known = {"--migrate-force", "--check-branches"}
    unknown = [a for a in args if a not in known]
    if unknown:
        print(
            f"ai_hats._bump_internal: unknown args: {' '.join(unknown)}",
            file=sys.stderr,
        )
        return 2

    # Converge the venv to what the new version actually declares.
    # Before do_bump, so a bump failure cannot leave a retired CLI reachable.
    # The import is inside the guard too: `retired_dists` reaches `_bootstrap`,
    # and an ImportError here would skip the re-assembly entirely.
    try:
        from pathlib import Path

        from .retired_dists import prune_retired

        for removed in prune_retired(Path.cwd()):
            print(f"ai-hats: removed retired {removed}", file=sys.stderr)
    except BaseException as exc:  # noqa: BLE001 - never let a prune cost the bump
        print(f"ai-hats: retired-distribution prune skipped: {exc!r}", file=sys.stderr)

    from .cli.assembly import do_bump

    return do_bump(migrate_force=migrate_force, check_branches=check_branches)


if __name__ == "__main__":
    sys.exit(main())
