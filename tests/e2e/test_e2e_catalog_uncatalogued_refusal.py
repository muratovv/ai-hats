"""e2e (HATS-1563)

flow:   the e2e catalog generator refuses an uncatalogued test file unless bypassed
cmds:
    bash scripts/gates.sh e2e-catalog
expect: gen_e2e_catalog.py exits non-zero naming uncatalogued files and remedy guidance
why:    without uncatalogued file refusal, new tests added without docstrings bypass
        the catalog check silently
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "gen_e2e_catalog.py"

# The pin is grounded in the BODY, not only the header: `_ids_known_for` strips
# the docstring on purpose, so a header pin's other basis is the file's git log —
# and this fixture lives in a tmp dir that is no repository (HATS-1644).
VALID_BLOCK = '''"""e2e (HATS-1563)

flow:   valid flow for fixture
cmds:
    echo valid
expect: valid outcome
why:    fixture requirement
"""

# HATS-1563
'''


@pytest.mark.integration
def test_uncatalogued_file_refusal_and_ack_bypass(tmp_path: Path):
    """HATS-1563: Uncatalogued test file causes gen_e2e_catalog.py to exit 1 with remedy guidance.

    Fail-under-revert: reverting scripts/gen_e2e_catalog.py to pre-fix baseline ->
    --check exits 1 with 'stale CATALOG.md' and instructions to run --write,
    and --write exits 0, failing assertions on remedy text and exit codes.
    """
    (tmp_path / "test_valid.py").write_text(VALID_BLOCK)
    (tmp_path / "test_blockless.py").write_text('"""prose only without block"""\n')

    # 1. --check mode refuses
    res_check = subprocess.run(
        [sys.executable, str(SCRIPT), "--check", "--dir", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert res_check.returncode == 1
    assert "refusal — 1 uncatalogued file(s):" in res_check.stderr
    assert "- test_blockless.py" in res_check.stderr
    assert "Remedy: write the four-field block" in res_check.stderr
    assert "run `python scripts/gen_e2e_catalog.py --write`" not in res_check.stderr

    # 2. --write mode writes CATALOG.md but still exits 1
    res_write = subprocess.run(
        [sys.executable, str(SCRIPT), "--write", "--dir", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert res_write.returncode == 1
    assert (tmp_path / "CATALOG.md").exists()
    assert "refusal — 1 uncatalogued file(s):" in res_write.stderr

    # 3. AI_HATS_E2E_CATALOG_ACK=1 bypasses refusal
    env = dict(os.environ, AI_HATS_E2E_CATALOG_ACK="1")
    res_ack_write = subprocess.run(
        [sys.executable, str(SCRIPT), "--write", "--dir", str(tmp_path)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert res_ack_write.returncode == 0
    assert "BYPASSED via AI_HATS_E2E_CATALOG_ACK=1" in res_ack_write.stderr

    res_ack_check = subprocess.run(
        [sys.executable, str(SCRIPT), "--check", "--dir", str(tmp_path)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert res_ack_check.returncode == 0
    assert "BYPASSED via AI_HATS_E2E_CATALOG_ACK=1" in res_ack_check.stderr
