"""Background-spawn entry point for `ai-hats reflect session --background`.

Invoked as: `python -m ai_hats.cli.reflect_session_main <session_id> <max_retries>`
Runs the in-process foreground path so subprocess Popen captures all output.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: reflect_session_main <session_id> [max_retries]", file=sys.stderr)
        return 2
    session_id = sys.argv[1]
    max_retries = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    from ..retro.reflect_session import ReflectSessionError, ReflectSessionRunner

    project_dir = Path.cwd()
    runner = ReflectSessionRunner(project_dir)
    try:
        path = runner.run(session_id, max_retries=max_retries)
    except ReflectSessionError as exc:
        print(f"reflect-session failed for {session_id}: {exc}", file=sys.stderr)
        return 2
    print(f"reflect-session saved to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
