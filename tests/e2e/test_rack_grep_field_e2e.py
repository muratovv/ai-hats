"""e2e (HATS-1324)

flow:   a developer filtering task list output by specific card attributes
cmds:
    rack ls --grep id:HATS-926 --json
expect: output contains only task cards matching the specified field prefix instead of
        matching general prose text
why:    field-prefixed grep filtering must match targeted card attributes to avoid false
        positives from general text descriptions
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.rack]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _rack(tasks_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Drive the real rack CLI in its own process against THIS checkout."""
    from _helpers.env import checkout_pythonpath

    env = os.environ.copy()
    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT, env.get("PYTHONPATH", ""))
    return subprocess.run(
        [sys.executable, "-m", "ai_hats_rack", *args, "--tasks-dir", str(tasks_dir)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _ids(proc: subprocess.CompletedProcess[str]) -> list[str]:
    assert proc.returncode == 0, proc.stderr
    return [row["id"] for row in json.loads(proc.stdout)["tasks"]]


@pytest.fixture
def two_cards(tmp_path):
    """One card whose id matches the needle, one that merely cites it."""
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir(parents=True)
    assert _rack(tasks_dir, "create", "S3 unmount", "--id", "HATS-9260").returncode == 0
    assert (
        _rack(
            tasks_dir,
            "create",
            "Follow-up",
            "--id",
            "HATS-9900",
            "--description",
            "split out of HATS-9260",
        ).returncode
        == 0
    )
    return tasks_dir


def test_bare_grep_finds_the_mention_not_the_card(two_cards):
    # The defect, at process level: the needle is an id, the haystack is prose.
    assert _ids(_rack(two_cards, "ls", "--grep", "HATS-926", "--json")) == ["HATS-9900"]


def test_field_targeted_grep_finds_the_card_itself(two_cards):
    assert _ids(_rack(two_cards, "ls", "--grep", "id:HATS-926", "--json")) == ["HATS-9260"]


def test_empty_pattern_after_the_field_is_refused(two_cards):
    refused = _rack(two_cards, "ls", "--grep", "id:", "--json")
    assert refused.returncode == 1, refused.stdout
