"""Selector uniqueness within a root (HATS-1545 R10 / D8).

A backlog's NAME became addressable in HATS-1545: a role writes
``apps.rack.<backlog>`` and the subscriber routes on it. ``instance_by_name``
answers the first match, so two instances answering to one selector would install
a declared gate on whichever the walk found first — silently, and on the wrong
backlog. This is the only new LOAD-time refusal in that change, so it gets its
own coverage: it can reject a project that loads today.
"""

from __future__ import annotations

import pytest

from ai_hats_rack.resolver import RackRoot
from ai_hats_rack.verbs.groups import DuplicateGroupNameError
from ai_hats_rack.workspace import (
    DuplicateBacklogNameError,
    Workspace,
    backlog_selectors_in_root,
)

pytestmark = pytest.mark.integration


def _backlog(name: str, prefix: str, *, cli_alias: str | None = None) -> str:
    alias = f"cli_alias: {cli_alias}\n" if cli_alias else ""
    return (
        f"name: {name}\nprefix: {prefix}\n{alias}"
        "fsm:\n  initial: a\n  states: [{name: a}, {name: b}]\n"
        "  edges: [{from: a, to: b}, {from: b, to: a}]\n"
        "links:\n  kinds: [{name: relates, arity: many}]\n"
    )


def _root(tmp_path, siblings: dict[str, str]) -> RackRoot:
    tracker = tmp_path / "proj" / ".agent" / "ai-hats" / "tracker"
    (tracker / "backlog" / "tasks").mkdir(parents=True)
    for dirname, text in siblings.items():
        d = tracker / dirname
        d.mkdir(parents=True)
        (d / "backlog.yaml").write_text(text, encoding="utf-8")
    return RackRoot(project_dir=tmp_path / "proj", tasks_dir=tracker / "backlog" / "tasks")


def test_two_backlogs_answering_to_one_name_refuse_at_load(tmp_path):
    """The first-match hazard, closed. Names the selector, not just 'a conflict'."""
    root = _root(tmp_path, {"hyp": _backlog("dup", "H"), "prop": _backlog("dup", "P")})

    with pytest.raises(DuplicateBacklogNameError) as exc:
        Workspace.discover([root])

    assert exc.value.name == "dup"
    assert "dup" in str(exc.value)


def test_a_name_colliding_with_a_siblings_alias_also_refuses(tmp_path):
    """``instance_by_name`` matches EITHER spelling, so the cross case routes by
    first match too — it cannot be left to the group-name guard."""
    root = _root(
        tmp_path,
        {"hyp": _backlog("hyp", "H", cli_alias="shared"), "prop": _backlog("shared", "P")},
    )

    with pytest.raises(DuplicateBacklogNameError):
        Workspace.discover([root])


def test_two_colliding_aliases_stay_with_the_group_guard(tmp_path):
    """Deliberately NOT this error: alias-vs-alias already has a typed refusal at
    the CLI group site, and preempting it would change which error a caller
    catches for a defect that was already loud."""
    root = _root(
        tmp_path,
        {
            "hyp": _backlog("hyp", "H", cli_alias="dup"),
            "prop": _backlog("prop", "P", cli_alias="dup"),
        },
    )

    workspace = Workspace.discover([root])

    assert {i.name for i in workspace.instances} == {"tasks", "hyp", "prop"}
    assert issubclass(DuplicateGroupNameError, Exception), "the guard that owns that surface"


def test_distinct_names_load_and_expose_every_selector(tmp_path):
    """The roster the check channel routes on: name AND alias, tasks included."""
    root = _root(tmp_path, {"hyp": _backlog("hyp", "H", cli_alias="hypothesis")})

    Workspace.discover([root])

    assert set(backlog_selectors_in_root(root)) == {"tasks", "hyp", "hypothesis"}
