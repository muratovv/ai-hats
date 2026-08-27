"""e2e (HATS-1853)

flow:   a library author commits prose that still carries a tracker id, and the
        pre-commit gate has to refuse it before the id ships to other projects
cmds:
    bash packages/ai-hats-library/src/ai_hats_library/usage/skills/ticket-id-gate/git_hooks/pre-commit-ticket-ids.sh
    python -m ai_hats.cli.githooks_hook pre-commit --project-dir . --githooks-dir .githooks
expect: the hook blocks on a staged `<PREFIX>-<digits>`, spares the `<PREFIX>-NNN`
        placeholder beside it, honours the same-line allow marker, learns the
        prefix from the project's own cards rather than carrying one, and its
        refusal survives a later permissive hook in the materialized chain.
why:    the hook is itself shipped library content, so a hardcoded prefix in it
        would BE the leak it refuses — the learning path is load-bearing, not a
        convenience. And a single-hook test cannot see a sibling hook overriding
        the verdict, which is how a blanket deny once shipped past a green suite,
        so the composite chain is driven here too.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _helpers.git import commit_file, git, init_repo

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = (
    REPO_ROOT
    / "packages/ai-hats-library/src/ai_hats_library/usage/skills/ticket-id-gate"
    / "git_hooks/pre-commit-ticket-ids.sh"
)
PROSE = "packages/ai-hats-library/src/ai_hats_library/core/skills/demo/SKILL.md"


def _repo(tmp_path: Path, body: str, *, card: str | None = "ACME-42", path: str = PROSE) -> Path:
    """A project with one staged prose file and, optionally, one tracker card."""
    root = tmp_path / "project"
    root.mkdir(parents=True)
    init_repo(root)
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)
    if card:
        (root / ".agent/ai-hats/tracker/backlog/tasks" / card).mkdir(parents=True)
    git(root, "add", "-A")
    return root


def _run(root: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for key in ("AI_HATS_TICKET_IDS_ACK", "AI_HATS_TICKET_PREFIX", "AI_HATS_DIR"):
        env.pop(key, None)
    env.update(extra)
    return subprocess.run(  # noqa: S603
        ["bash", str(HOOK)],  # noqa: S607
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def test_a_placeholder_alone_is_not_a_ticket_id(tmp_path: Path):
    """Digits are the whole discrimination: a CLI template must survive."""
    root = _repo(tmp_path, "Run `rack ls ACME-NNN` to read a card.\n")
    done = _run(root)
    assert done.returncode == 0, done.stdout + done.stderr


def test_it_blocks_the_id_sharing_a_line_with_a_placeholder(tmp_path: Path):
    """The other direction, with both forms on ONE line — a checker keyed on the
    line rather than the token would have to fail one of these two tests."""
    root = _repo(tmp_path, "Run `rack ls ACME-NNN`; the form landed in ACME-1430.\n")
    done = _run(root)
    combined = done.stdout + done.stderr
    assert done.returncode == 1, combined
    assert "ACME-1430" in combined, combined


def test_the_prefix_is_learned_from_the_project_not_carried_by_the_hook(tmp_path: Path):
    """The hook ships to other projects; a prefix written into it would be the leak.

    Nothing here mentions this repository's prefix — the card directory is the
    only place `ACME` appears, and the refusal has to come back naming it.
    """
    root = _repo(tmp_path, "history lives in ACME-1430.\n", card="ACME-7")
    done = _run(root)
    combined = done.stdout + done.stderr
    assert done.returncode == 1, combined
    assert "ACME-1430" in combined, combined


def test_a_project_with_no_cards_is_a_loud_no_op(tmp_path: Path):
    """No tracker means no ids to leak — and the skip has to be audible."""
    root = _repo(tmp_path, "mentions ACME-1430.\n", card=None)
    done = _run(root)
    combined = done.stdout + done.stderr
    assert done.returncode == 0, combined
    assert "no tracker prefix found" in combined, combined


def test_the_same_line_marker_keeps_an_id_a_machine_prints(tmp_path: Path):
    root = _repo(
        tmp_path,
        "a wall of ACME-1242 errors  <!-- ticket-ids: allow the guard prints it -->\n",
    )
    done = _run(root)
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_ack_flag_is_named_by_the_refusal_and_works(tmp_path: Path):
    """A deny that does not name its hatch leaves the author nowhere to go."""
    root = _repo(tmp_path, "history lives in ACME-1430.\n")
    refused = _run(root)
    assert "AI_HATS_TICKET_IDS_ACK=1" in refused.stdout + refused.stderr

    allowed = _run(root, AI_HATS_TICKET_IDS_ACK="1")
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr


def test_hook_scripts_are_out_of_scope(tmp_path: Path):
    """An id in code is often the whole comment — removing it is a rewrite."""
    root = _repo(
        tmp_path,
        "carried over from ACME-1430.\n",
        path="packages/ai-hats-library/src/ai_hats_library/core/skills/demo/hooks/note.md",
    )
    done = _run(root)
    assert done.returncode == 0, done.stdout + done.stderr


def test_a_tracked_but_unstaged_id_does_not_retro_block_the_commit(tmp_path: Path):
    """Changed-files scope: the gate fires on what THIS commit touches.

    The id has to be in a file git already tracks, or the test proves nothing —
    an untracked file is invisible to whole-tree scans too, so a hook that had
    quietly widened to `git ls-files` would still pass.
    """
    root = _repo(tmp_path, "clean prose.\n")
    git(root, "commit", "-m", "base")

    neighbour = (root / PROSE).parent / "OTHER.md"
    commit_file(root, neighbour.relative_to(root), "clean too.\n", "neighbour")

    neighbour.write_text("history lives in ACME-1430.\n")  # tracked, NOT staged
    (root / PROSE).write_text("still clean, and this one IS staged.\n")
    git(root, "add", PROSE)

    done = _run(root)
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_refusal_survives_a_later_permissive_hook_in_the_chain(tmp_path: Path):
    """The composite verdict, through the real dispatcher.

    A single-hook test cannot observe a sibling overriding the verdict. Here a
    permissive drop-in runs after this gate, and the chain must still refuse —
    git's contract is the first non-zero exit, and that is what is asserted.
    """
    root = _repo(tmp_path, "history lives in ACME-1430.\n")
    dropins = root / ".githooks" / "pre-commit.d"
    dropins.mkdir(parents=True)
    shutil.copy(HOOK, dropins / "10-ticket-ids.sh")
    (dropins / "20-permissive.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
    for script in dropins.iterdir():
        script.chmod(0o755)

    env = os.environ.copy()
    for key in ("AI_HATS_TICKET_IDS_ACK", "AI_HATS_TICKET_PREFIX"):
        env.pop(key, None)
    done = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "ai_hats.cli.githooks_hook",
            "pre-commit",
            "--project-dir",
            str(root),
            "--githooks-dir",
            str(root / ".githooks"),
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    combined = done.stdout + done.stderr
    assert done.returncode != 0, combined
    assert "ACME-1430" in combined, combined
