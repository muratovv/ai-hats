from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make rack_testkit importable regardless of pytest's rootdir/sys.path mode.
sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture
def tasks_dir(tmp_path) -> Path:
    return tmp_path / "tasks"


@pytest.fixture
def cwd(tmp_path) -> Path:
    return tmp_path
