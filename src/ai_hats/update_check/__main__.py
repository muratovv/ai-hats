"""Background entry-point: ``python -m ai_hats.update_check <project_dir> <cache_root>``.

Spawned detached by the ``check_update_async`` pipeline step. Stdout/stderr
go to ``DEVNULL`` from the parent, so this module never writes to terminal —
its only side effect is writing the cache file. Exit code is ignored.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ai_hats_core.layout import CacheLayout

from . import is_disabled, run_check


def main() -> int:
    if is_disabled():
        return 0
    if len(sys.argv) < 3:
        return 1
    project_dir = Path(sys.argv[1]).resolve()
    if not project_dir.is_dir():
        return 1
    try:
        run_check(project_dir, CacheLayout(Path(sys.argv[2])))
    except Exception:  # silent-ok: the exit code is ignored by the caller, by contract
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
