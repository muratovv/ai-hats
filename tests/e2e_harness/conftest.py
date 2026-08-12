"""In-process tests OF the e2e harness — the scaffolding, not a real CLI run.

Their subject is ``tests/e2e/_helpers/*`` and ``tests/e2e/conftest.py``; nothing
here spawns a subprocess, so ``integration`` would be a lie and ``tests/e2e/``
the wrong home (HATS-1600). Here they ride the ``unit`` stage, where their ~2 s
belongs. The insert below is the whole reason this file exists: ``_helpers`` is
importable only while ``tests/e2e/`` is on ``sys.path``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "e2e"))
