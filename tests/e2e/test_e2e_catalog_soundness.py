"""e2e (HATS-1561)

flow:   a maintainer runs e2e-catalog --check when an e2e test carries an unsound flow block
cmds:
    python scripts/gen_e2e_catalog.py --check
expect: script exits non-zero and outputs an unsound row refusal naming file, command and reason
why:    prevents mechanical catalog corruptions (invented pins, non-resolving commands, plumbing) from landing
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_e2e_catalog_check_refuses_unsound_flow_block(tmp_path: Path) -> None:
    """Proves that gen_e2e_catalog.py --check refuses an unsound flow block via real subprocess."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    shutil.copy(_REPO_ROOT / "scripts" / "gen_e2e_catalog.py", scripts_dir / "gen_e2e_catalog.py")
    e2e_dir = tmp_path / "tests" / "e2e"
    e2e_dir.mkdir(parents=True)

    unsound_test = e2e_dir / "test_unsound.py"
    unsound_test.write_text(
        '"""e2e (HATS-1561)\n'
        "\n"
        "flow:   a maintainer tests an unsound block\n"
        "cmds:\n"
        "    ai-hats status\n"
        "expect: failure\n"
        "why:    test\n"
        '"""\n',
        encoding="utf-8",
    )

    from scripts.gen_e2e_catalog import collect, render

    rows, pending, _ = collect(e2e_dir)
    (e2e_dir / "CATALOG.md").write_text(render(rows, pending), encoding="utf-8")

    # git init + commit for log lookup
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.x"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init", "--quiet"], cwd=tmp_path, check=True)

    res = subprocess.run(
        [sys.executable, str(scripts_dir / "gen_e2e_catalog.py"), "--check"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert res.returncode == 1
    # An unsound ROW, not a malformed block: the block parses, so the reader must
    # be sent to what it says, not to its shape (HATS-1644).
    assert "[e2e-catalog] unsound row(s):" in res.stderr
    assert "test_unsound.py" in res.stderr
    # Named as the user types it. The older wording said "under main" — click's
    # internal group name, which appears on no command line.
    assert "unknown subcommand 'status' for command 'ai-hats'" in res.stderr
    assert res.stderr.count("unknown subcommand 'status'") == 1, (
        f"one defect, reported once — two resolvers both owned `ai-hats` lines "
        f"before HATS-1644:\n{res.stderr}"
    )
