"""What the git dispatcher spawns (HATS-1337).

A MODULE, never the ``githooks resolve`` subcommand — an unregistered subcommand
is treated as a bare prompt and launches a provider session
(``_PassthroughGroup``, HATS-1202), so under version skew a `git commit` would
start an agent session that overwrites the hook it is running. Its own file so
``-m`` does not re-execute a module ``cli/__init__`` already imported (runpy
warns on stderr, in front of a human, every commit).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    from ..assembler import Assembler
    from .githooks import build_records

    parser = argparse.ArgumentParser(prog="ai_hats.cli.githooks_main")
    parser.add_argument("event")
    parser.add_argument("--project-dir", required=True, type=Path)
    args = parser.parse_args(argv)

    out, err = build_records(Assembler(args.project_dir), args.event)
    for line in err:
        print(line, file=sys.stderr)
    for line in out:
        print(line)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    sys.exit(main())
