"""HATS-693: `task show` hardening (sub-agent review follow-up to HATS-691).

- Index markup-escape: user-controlled card titles can't inject rich markup.
- Long-output hint: a long Linked-context block nudges toward `--short`.
- Parity invariant: the CLI renders the exact same assembler output a sub-agent
  receives, and both callers share `linked_context.load_linked_context`.
- CLI edge paths: dangling link, no links.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from ai_hats.cli.task import task
from ai_hats.linked_context import load_linked_context
from ai_hats.models import TaskCard, TaskState
from ai_hats.observe import SessionManager
from ai_hats.paths import runs_dir, tasks_dir
from ai_hats.runtime import SubAgentRunner


def _null_payload(**kw):
    """Minimal CompositionPayload for helper-method seams (HATS-865)."""
    from ai_hats.composition_payload import CompositionPayload
    from ai_hats_core import CompositionResult

    return CompositionPayload(
        result=CompositionResult(
            name="t", priorities=[], rules=[], skills=[], injections=[],
        ),
        provider=None,
        effective_role="t",
        **kw,
    )


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch) -> Path:
    pd = tmp_path / "proj"
    tasks_dir(pd).mkdir(parents=True)
    monkeypatch.chdir(pd)
    return pd


def _write(pd: Path, card: TaskCard, plan_body: str | None = None) -> None:
    card_dir = tasks_dir(pd) / card.id
    card.save(card_dir / "task.yaml")
    if plan_body is not None:
        (card_dir / "plan.md").write_text(plan_body)


# ---- markup-escape ----


def test_show_escapes_markup_in_own_title(project_dir: Path):
    _write(project_dir, TaskCard(id="HATS-902", title="[red]EVIL[/]", state=TaskState.EXECUTE))
    for args in (["show", "HATS-902"], ["show", "HATS-902", "--short"]):
        res = CliRunner().invoke(task, args)
        assert res.exit_code == 0, res.output
        # The literal tag survives instead of being eaten/recolored by rich.
        assert "[red]EVIL[/]" in res.output, (args, res.output)


def test_show_escapes_markup_in_linked_index_title(project_dir: Path):
    _write(project_dir, TaskCard(id="HATS-901", title="[bold]REL[/]", state=TaskState.DONE))
    _write(
        project_dir,
        TaskCard(id="HATS-902", title="child", state=TaskState.EXECUTE, related=["HATS-901"]),
    )
    res = CliRunner().invoke(task, ["show", "HATS-902"])
    assert res.exit_code == 0, res.output
    # The related card's title appears literally in the "Related:" index.
    assert "[bold]REL[/]" in res.output, res.output


# ---- edge paths ----


def test_show_dangling_link(project_dir: Path):
    _write(
        project_dir,
        TaskCard(id="HATS-902", title="child", state=TaskState.EXECUTE, related=["HATS-404"]),
    )
    res = CliRunner().invoke(task, ["show", "HATS-902"])
    assert res.exit_code == 0, res.output
    assert "(missing)" in res.output  # index flags the dangling ref
    assert "Linked context:" not in res.output  # nothing resolvable to render


def test_show_no_links_omits_heading(project_dir: Path):
    _write(project_dir, TaskCard(id="HATS-902", title="lonely", state=TaskState.EXECUTE))
    res = CliRunner().invoke(task, ["show", "HATS-902"])
    assert res.exit_code == 0, res.output
    assert "Linked context:" not in res.output


# ---- long-output hint ----


def test_show_long_linked_emits_short_hint(project_dir: Path):
    long_plan = "# EPIC PLAN\n" + "\n".join(f"plan line {i}" for i in range(60))
    _write(
        project_dir,
        TaskCard(id="HATS-900", title="Epic", state=TaskState.EXECUTE, description="epic"),
        plan_body=long_plan,
    )
    _write(
        project_dir,
        TaskCard(id="HATS-902", title="child", state=TaskState.EXECUTE, parent_task="HATS-900"),
    )
    res = CliRunner().invoke(task, ["show", "HATS-902"])
    assert res.exit_code == 0, res.output
    assert "--short" in res.output and "tip:" in res.output
    # --short never carries the hint (no linked block at all).
    short = CliRunner().invoke(task, ["show", "HATS-902", "--short"])
    assert "tip:" not in short.output


def test_show_short_linked_has_no_hint(project_dir: Path):
    _write(project_dir, TaskCard(id="HATS-900", title="Epic", state=TaskState.EXECUTE, description="e"))
    _write(
        project_dir,
        TaskCard(id="HATS-902", title="child", state=TaskState.EXECUTE, parent_task="HATS-900"),
    )
    res = CliRunner().invoke(task, ["show", "HATS-902"])
    assert res.exit_code == 0, res.output
    assert "Linked context:" in res.output  # block present…
    assert "tip:" not in res.output  # …but short, so no hint


# ---- parity invariant ----


def test_cli_and_subagent_share_assembler(project_dir: Path):
    _write(
        project_dir,
        TaskCard(id="HATS-900", title="Epic", state=TaskState.EXECUTE, description="EPIC BODY"),
        plan_body="# P\nPLAN BODY",
    )
    _write(
        project_dir,
        TaskCard(id="HATS-902", title="child", state=TaskState.EXECUTE, parent_task="HATS-900"),
    )
    body = load_linked_context(tasks_root=tasks_dir(project_dir), ticket_id="HATS-902")
    assert body  # sanity

    # Sub-agent path uses the very same seam (HATS-691 extraction).
    assert SubAgentRunner(
        project_dir,
        _null_payload(),
        session_mgr=SessionManager(project_dir, runs_dir=runs_dir(project_dir)),
    )._load_linked_context("HATS-902") == body

    # CLI path renders that exact assembler output verbatim.
    res = CliRunner().invoke(task, ["show", "HATS-902"])
    assert res.exit_code == 0, res.output
    assert body in res.output
