"""The cross-project root set, keyed on the BACKLOG rather than the anchor.

HATS-1573: ``--tasks-dir`` moves the backlog without moving the caller's
project, so two roots can share a ``project_dir`` and still be two different
backlogs. Deduping on the anchor silently dropped one of them.
"""

from __future__ import annotations

from ai_hats_rack.cli_common import resolve_roots


def test_a_scratch_backlog_does_not_swallow_the_projects_own(tmp_path):
    project = tmp_path / "proj"
    (project / ".agent").mkdir(parents=True)
    scratch = tmp_path / "scratch" / "tasks"
    scratch.mkdir(parents=True)

    roots = resolve_roots(scratch, project, (str(project),))

    assert [r.tasks_dir for r in roots] == [
        scratch,
        project / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks",
    ]
    assert {r.project_dir for r in roots} == {project}  # one anchor, two backlogs
    assert [r.backlog_owner for r in roots] == [None, project]


def test_the_same_backlog_named_twice_is_one_root(tmp_path):
    project = tmp_path / "proj"
    (project / ".agent").mkdir(parents=True)

    roots = resolve_roots(None, project, (str(project),))

    assert len(roots) == 1
