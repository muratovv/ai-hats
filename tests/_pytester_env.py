"""Env plumbing for pytester self-tests that copy the real conftest verbatim.

The copied conftest imports ``tests._repo_integrity`` (HATS-887); a pytester
inner subprocess gets only ``os.getcwd()`` (the pytester tmpdir) on
``PYTHONPATH``, so ``tests`` is unresolvable there (HATS-916). Threading this
checkout's root through the outer env — ``Pytester.popen`` appends it — puts
the package back on the inner ``sys.path``.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def pythonpath_with_repo_root() -> str:
    """Outer-env PYTHONPATH value that makes ``tests`` importable inner-side."""
    return os.pathsep.join(
        filter(None, [str(REPO_ROOT), os.environ.get("PYTHONPATH", "")])
    )
