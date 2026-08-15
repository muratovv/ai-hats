from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from pathlib import Path

from ai_hats_observe import SessionManager


@contextmanager
def _umask(mask: int):
    previous = os.umask(mask)
    try:
        yield
    finally:
        os.umask(previous)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_create_session_directory_is_private_under_permissive_umask(tmp_path: Path) -> None:
    with _umask(0o022):
        session = SessionManager(runs_dir=tmp_path / "runs").create_session()

    assert _mode(session.session_dir) == 0o700
