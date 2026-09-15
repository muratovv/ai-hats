"""e2e (HATS-1659)

flow:   a maintainer runs a gate from a shell with no venv on PATH, and a test
        the stage runs spawns `pytest` or `python3` by bare name
cmds:
    bash scripts/gates.sh python-pin
    PYTHON=/elsewhere/bin/python bash scripts/gates.sh python-pin
expect: the stage's children find the stage's own interpreter first on PATH,
        whether it came from the checkout's .venv or from PYTHON=
why:    CI installs into the interpreter that IS on PATH, so PATH's python and
        the one running the suite agree there for free; locally the stages ran
        under <checkout>/.venv while PATH named the caller's shell, and three
        e2e tests went red on a python3 with no pytest in it
"""  # comment-length: allow — the e2e catalog header format

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from _helpers.git import git, init_repo

pytestmark = [pytest.mark.integration, pytest.mark.gates]

REPO_ROOT = Path(__file__).resolve().parents[2]
DISPATCHER = REPO_ROOT / "scripts" / "gates.sh"

#: A stage whose whole body is one `$PY` call, so a fake interpreter is the stage.
STAGE = "python-pin"


def _recording_python(path: Path, record: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "$PATH" > "{record}"\nexit 0\n')
    path.chmod(0o755)
    return path


@pytest.fixture()
def checkout(tmp_path: Path) -> Path:
    """A repository carrying this repo's real dispatcher and nothing else of it."""
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    init_repo(root)
    shutil.copy(DISPATCHER, root / "scripts" / "gates.sh")
    (root / "scripts" / "check_python_pin.py").write_text("")
    git(root, "add", "-A")
    git(root, "commit", "-m", "a checkout with a stage to run")
    return root


def _stage(root: Path, marker: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "PYTHON"}
    env["PATH"] = os.pathsep.join([str(marker), "/usr/bin", "/bin"])
    env.update(extra)
    return subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["bash", "scripts/gates.sh", STAGE],
        cwd=root,
        capture_output=True,
        text=True,
        env=env,
    )


def _entries(record: Path) -> list[str]:
    assert record.is_file(), "the stage never reached its interpreter — nothing to measure"
    return record.read_text().strip().split(os.pathsep)


def test_the_checkouts_venv_leads_path_for_the_stage(checkout: Path, tmp_path: Path):
    record = tmp_path / "path.txt"
    marker = tmp_path / "callers" / "bin"
    marker.mkdir(parents=True)
    _recording_python(checkout / ".venv" / "bin" / "python", record)

    res = _stage(checkout, marker)

    assert res.returncode == 0, res.stderr
    entries = _entries(record)
    # The caller's PATH is still there: this is the control that says the
    # recorder saw a real PATH, not that the lead was the only entry.
    assert str(marker) in entries, entries
    assert entries[0] == str(checkout / ".venv" / "bin"), entries


def test_a_python_override_leads_path_the_same_way(checkout: Path, tmp_path: Path):
    record = tmp_path / "path.txt"
    marker = tmp_path / "callers" / "bin"
    marker.mkdir(parents=True)
    other = _recording_python(tmp_path / "other" / "bin" / "python", record)

    res = _stage(checkout, marker, PYTHON=str(other))

    assert res.returncode == 0, res.stderr
    entries = _entries(record)
    assert str(marker) in entries, entries
    assert entries[0] == str(other.parent), entries
