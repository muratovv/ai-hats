"""e2e (HATS-1866)

flow:   an agent runs a documented link command against the wrong backlog and has
        to be able to tell "this kind does not exist" from "not on THIS backlog"
cmds:
    python -m ai_hats_rack transition HATS-1 --link orders_of:HATS-2
    python -m ai_hats_rack transition HATS-1 --link nosuchkind:HATS-2
expect: the refusal names the backlog whose kind set it lists, and adds the
        sibling that declares the kind — but only when a sibling really does
why:    the message without an owner reads as absolute. One session took a
        correct instruction from a shipped skill, ran it against a task card,
        read this refusal as proof the instruction was wrong, and filed a card to
        edit three working CLI templates. The sibling backlog is mounted here so
        the hint has something true to say, and a kind belonging to nobody is
        asserted in the same run — a hint that always fires is a lie, not a help.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: A sibling catalog declaring a kind the tasks backlog does not. Named for
#: nothing in this repository on purpose — the hint has to be read off whatever
#: is mounted. (The subject ids keep the packaged tasks prefix; only a fixture
#: with its own tasks backlog.yaml could change that, and it is not the subject.)
SIBLING = """\
name: orders
prefix: ORD
cli_alias: order
fsm:
  initial: open
  states:
    - { name: open }
    - { name: closed }
  edges:
    - { from: open, to: closed, name: close }
links:
  kinds:
    - { name: orders_of, arity: many, targets: tasks }
"""


def _tracker(tmp_path: Path) -> Path:
    """The conventional layout — sibling discovery only walks `tracker/backlog`."""
    tasks = tmp_path / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks"
    tasks.mkdir(parents=True)
    sibling = tasks.parent / "orders"
    sibling.mkdir()
    (sibling / "backlog.yaml").write_text(SIBLING)
    return tasks


def _rack(tasks_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Drive the real rack CLI in its own process against THIS checkout."""
    from _helpers.env import checkout_pythonpath

    env = os.environ.copy()
    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT, env.get("PYTHONPATH", ""))
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "ai_hats_rack", *args, "--tasks-dir", str(tasks_dir)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.fixture
def tracker(tmp_path: Path) -> Path:
    tasks = _tracker(tmp_path)
    _rack(tasks, "create", "subject", "--id", "HATS-1")
    _rack(tasks, "create", "target", "--id", "HATS-2")
    return tasks


def test_the_refusal_names_the_backlog_whose_kinds_it_lists(tracker: Path):
    refusal = _rack(tracker, "transition", "HATS-1", "--link", "nosuchkind:HATS-2")
    combined = refusal.stdout + refusal.stderr
    assert refusal.returncode != 0, combined
    assert "'tasks' backlog" in combined, combined


def test_a_sibling_backlogs_kind_is_named_as_such(tracker: Path):
    """The sentence that turns a dead end into a next step."""
    refusal = _rack(tracker, "transition", "HATS-1", "--link", "orders_of:HATS-2")
    combined = refusal.stdout + refusal.stderr
    assert refusal.returncode != 0, combined
    assert "'orders' backlog" in combined, combined


def test_a_kind_belonging_to_nobody_gets_no_sibling_hint(tracker: Path):
    """The other direction, in the same mounted tree — so the absence of the hint
    is a decision the code made, not an absence of siblings to find."""
    refusal = _rack(tracker, "transition", "HATS-1", "--link", "nosuchkind:HATS-2")
    combined = refusal.stdout + refusal.stderr
    assert "Unknown link kind 'nosuchkind'" in combined, combined
    assert "is a kind of" not in combined, combined
    assert "orders" not in combined, combined


def test_a_declared_kind_still_links(tracker: Path):
    """The refusal work must not cost the working path."""
    done = _rack(tracker, "transition", "HATS-1", "--link", "related:HATS-2")
    combined = done.stdout + done.stderr
    assert done.returncode == 0, combined
    assert "Linked" in combined, combined
