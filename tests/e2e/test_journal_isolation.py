# comment-length: allow — the e2e catalog requires this flow block
"""e2e (HATS-1671)

flow:   a developer runs the hook tests in a checkout that already has a guard journal
cmds:
    python -m pytest tests/e2e/test_py_security_lint_hook.py
expect: the checkout's .git/ai-hats/bypasses.jsonl is byte-identical afterwards
why:    a hook whose process cwd is the checkout used to append a row with the real branch and HEAD
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

# The catalog pin is grounded here; the module docstring is stripped before that check.
REPO_ROOT = Path(__file__).resolve().parents[2]  # HATS-1671
ENV_JOURNAL_DIR = "AI_HATS_JOURNAL_DIR"
TARGETS = (
    "tests/e2e/test_py_security_lint_hook.py",
    "tests/e2e/test_wt_entry_gate_hook.py",
    "tests/test_ack_prefix_guard.py",
)


def _live_journal_dir() -> Path:
    common = subprocess.run(  # noqa: S603
        ["git", "rev-parse", "--git-common-dir"],  # noqa: S607
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    root = Path(common)
    if not root.is_absolute():
        root = REPO_ROOT / root
    return root.resolve() / "ai-hats"


def _digest(path: Path) -> str:
    if not path.is_file():
        return "<absent>"
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.integration
@pytest.mark.parametrize("target", TARGETS)
def test_a_suite_run_does_not_touch_the_live_journal(target: str) -> None:
    journal_dir = _live_journal_dir()
    watched = {name: journal_dir / name for name in ("bypasses.jsonl", "catches.jsonl")}
    before = {name: _digest(path) for name, path in watched.items()}

    env = os.environ.copy()
    env.pop(ENV_JOURNAL_DIR, None)
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", target, "-q", "-p", "no:randomly", "--tb=line"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]

    after = {name: _digest(path) for name, path in watched.items()}
    drifted = [name for name in watched if before[name] != after[name]]
    assert not drifted, f"running {target} wrote the live guard journal(s) {drifted}"
