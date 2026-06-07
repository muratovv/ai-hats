"""e2e (HATS-691): real `ai-hats task show <id>` renders linked-task context.

Drives the real binary in a temp project: create an epic (with a `plan.md`) +
a related release + a child linked to both, then `ai-hats task show <child>`.
Default output must carry the linked bodies (epic description + epic `plan.md`
+ related card); `--short` must omit them while keeping the compact index.

Fail-under-revert: before HATS-691, default `task show` lists only link
IDs/state/title — none of the linked *bodies* — so the default-view assertions
fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _task_dir(root: Path, task_id: str) -> Path:
    return root / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks" / task_id


def test_task_show_renders_linked_context(tmp_project):
    proj = tmp_project

    # Epic (+ plan.md) — the parent that carries design context.
    proj.run("task", "create", "Epic train", "--id", "HATS-900",
             "-d", "EPIC DESCRIPTION BODY").expect_ok()
    _task_dir(proj.path, "HATS-900").joinpath("plan.md").write_text(
        "# EPIC PLAN\nEPIC PLAN BODY LINE\n"
    )

    # Related release.
    proj.run("task", "create", "Release 1.2", "--id", "HATS-901",
             "-d", "RELEASE BODY").expect_ok()

    # Child: parent_task -> epic, related -> release.
    proj.run("task", "create", "Bug in release", "--id", "HATS-902",
             "--parent-task", "HATS-900", "-d", "child body").expect_ok()
    proj.run("task", "link", "HATS-902", "HATS-901", "--type", "related").expect_ok()

    # Default show → full linked context.
    res = proj.run("task", "show", "HATS-902").expect_ok()
    res.expect_stdout_contains(
        "Linked context:",
        "EPIC DESCRIPTION BODY",  # parent epic card
        "EPIC PLAN BODY LINE",    # parent epic plan.md
        "RELEASE BODY",           # related card
    )

    # --short → compact index only, no linked bodies.
    short = proj.run("task", "show", "HATS-902", "--short").expect_ok()
    assert "Linked context:" not in short.stdout, short.stdout
    assert "EPIC PLAN BODY LINE" not in short.stdout, short.stdout
    assert "RELEASE BODY" not in short.stdout, short.stdout
    # Compact cross-reference index is still present.
    assert "HATS-901" in short.stdout, short.stdout


def test_task_show_escapes_markup_in_title(tmp_project):
    """HATS-693: a card title carrying rich markup renders literally, not eaten.

    Fail-under-revert: before the markup-escape hardening, `task show` printed
    the title with markup enabled, so `[bold]X[/]` was consumed by rich and the
    literal tag never appeared in stdout.
    """
    proj = tmp_project
    proj.run("task", "create", "[bold]INJECT[/] title", "--id", "HATS-700").expect_ok()
    res = proj.run("task", "show", "HATS-700").expect_ok()
    assert "[bold]INJECT[/]" in res.stdout, res.stdout
