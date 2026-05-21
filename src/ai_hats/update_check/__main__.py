"""Background entry-point: ``python -m ai_hats.update_check <project_dir>``.

Spawned detached by the ``check_update_async`` pipeline step. Stdout/stderr
go to ``DEVNULL`` from the parent, so this module never writes to terminal —
its only side effect is writing the cache file. Exit code is ignored.
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import is_disabled, run_check


def main() -> int:
    if is_disabled():
        return 0
    if len(sys.argv) < 2:
        return 1
    project_dir = Path(sys.argv[1]).resolve()
    if not project_dir.is_dir():
        return 1
    try:
        run_check(project_dir)
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
