"""HATS-1036 steps 4–5: per-backlog groups on the `rack` CLI.

The base surface is exactly the top-level verbs until sibling catalogs are
mounted (R2 surface re-pin); a mounted NON-tasks backlog becomes a group carrying a
schema-driven `create`, an `update` sugar, and the verbs its extensions
contribute via `verbs()` (`hyp append-verdict`/`autoclose`, `proposal vote`).
Ids route by prefix, so `transition` edge-name sugar (`refute`/`accept`) rides
the same workspace.
"""

from __future__ import annotations

import json

import click
import pytest
from click.testing import CliRunner

from ai_hats_rack.cli import main
from ai_hats_rack.definition import packaged_definition_source
from ai_hats_rack.verbs.groups import DuplicateGroupNameError


@pytest.fixture
def runner():
    return CliRunner()


def _tasks_catalog(tmp_path, *, with_siblings: bool):
    """A conventional tracker layout: tasks (packaged default topology, so the
    wired provider's epic-automation composes) + optional hyp/proposal siblings."""
    tracker = tmp_path / "proj" / ".agent" / "ai-hats" / "tracker"
    tasks = tracker / "backlog" / "tasks"
    tasks.mkdir(parents=True)
    if with_siblings:
        for name in ("hypotheses", "proposals"):
            d = tracker / name
            d.mkdir(parents=True)
            (d / "backlog.yaml").write_text(packaged_definition_source(name), encoding="utf-8")
    return tasks


def _env(tasks):
    return {"RACK_TASKS_DIR": str(tasks)}


def _run(runner, tasks, *args):
    return runner.invoke(main, list(args), env=_env(tasks), catch_exceptions=False)


def _json(result):
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


# ----- surface re-pin (R2) ---------------------------------------------------


_BASE_VERBS = {"create", "ls", "context", "transition", "plan-extract"}


def test_base_surface_is_the_base_verbs():
    # The base registration is the fixed top-level verbs — groups are a lazy
    # overlay (list_commands), never part of .commands.
    assert set(main.commands) == _BASE_VERBS


def test_no_groups_without_siblings(monkeypatch, tmp_path):
    tasks = _tasks_catalog(tmp_path, with_siblings=False)
    monkeypatch.setenv("RACK_TASKS_DIR", str(tasks))
    assert set(main.list_commands(click.Context(main))) == _BASE_VERBS


def test_groups_appear_when_mounted(monkeypatch, tmp_path):
    tasks = _tasks_catalog(tmp_path, with_siblings=True)
    monkeypatch.setenv("RACK_TASKS_DIR", str(tasks))
    listed = set(main.list_commands(click.Context(main)))
    assert _BASE_VERBS | {"hyp", "proposal"} == listed


def test_group_resolves_and_exposes_its_verbs(runner, tmp_path):
    tasks = _tasks_catalog(tmp_path, with_siblings=True)
    out = _run(runner, tasks, "hyp", "--help")
    assert out.exit_code == 0
    for verb in ("create", "update", "append-verdict", "autoclose"):
        assert verb in out.output
    prop = _run(runner, tasks, "proposal", "--help")
    assert "vote" in prop.output and "create" in prop.output


def test_unmounted_group_name_is_no_such_command(runner, tmp_path):
    tasks = _tasks_catalog(tmp_path, with_siblings=False)
    out = _run(runner, tasks, "hyp", "create", "x")
    assert out.exit_code == 2
    assert "No such command" in out.output


# ----- cli_alias drives the group name (verbs stay backlog-agnostic) ----------


def _minimal_backlog(name: str, prefix: str, *, cli_alias: str | None = None) -> str:
    """A load-valid catalog with a two-state fsm + one trivial kind — enough to
    mount, so a group name resolves from its (optional) cli_alias."""
    alias = f"cli_alias: {cli_alias}\n" if cli_alias else ""
    return (
        f"name: {name}\nprefix: {prefix}\n{alias}"
        "fsm:\n  initial: a\n  states: [{name: a}, {name: b}]\n"
        "  edges: [{from: a, to: b}, {from: b, to: a}]\n"
        "links:\n  kinds: [{name: relates, arity: many}]\n"
    )


def _tracker_with_siblings(tmp_path, siblings: dict[str, str]):
    """A tracker with the packaged tasks catalog plus arbitrary sibling catalogs
    (``dir name -> backlog.yaml text``)."""
    tracker = tmp_path / "proj" / ".agent" / "ai-hats" / "tracker"
    (tracker / "backlog" / "tasks").mkdir(parents=True)
    for dirname, text in siblings.items():
        d = tracker / dirname
        d.mkdir(parents=True)
        (d / "backlog.yaml").write_text(text, encoding="utf-8")
    return tracker / "backlog" / "tasks"


def test_group_name_uses_declared_cli_alias(monkeypatch, tmp_path):
    tasks = _tracker_with_siblings(
        tmp_path, {"widgets": _minimal_backlog("widgets", "WID", cli_alias="wid")}
    )
    monkeypatch.setenv("RACK_TASKS_DIR", str(tasks))
    listed = set(main.list_commands(click.Context(main)))
    assert "wid" in listed and "widgets" not in listed


def test_group_name_falls_back_to_name_without_cli_alias(monkeypatch, tmp_path):
    tasks = _tracker_with_siblings(tmp_path, {"widgets": _minimal_backlog("widgets", "WID")})
    monkeypatch.setenv("RACK_TASKS_DIR", str(tasks))
    assert "widgets" in set(main.list_commands(click.Context(main)))


def test_duplicate_effective_group_name_is_typed_error(monkeypatch, tmp_path):
    tasks = _tracker_with_siblings(
        tmp_path,
        {
            "alpha": _minimal_backlog("alpha", "ALPHA", cli_alias="dup"),
            "beta": _minimal_backlog("beta", "BETA", cli_alias="dup"),
        },
    )
    monkeypatch.setenv("RACK_TASKS_DIR", str(tasks))
    with pytest.raises(DuplicateGroupNameError) as exc_info:
        main.list_commands(click.Context(main))
    assert exc_info.value.group == "dup"


# ----- hyp flow (create → append-verdict → autoclose → refute → update) ------


def test_hyp_create_supplies_required_field(runner, tmp_path):
    tasks = _tasks_catalog(tmp_path, with_siblings=True)
    payload = _json(
        _run(runner, tasks, "hyp", "create", "an idea", "--hypothesis", "X causes Y", "--json")
    )["task"]
    assert payload["id"] == "HYP-001"
    assert payload["state"] == "active"
    assert payload["hypothesis"] == "X causes Y"


def test_hyp_create_without_required_field_is_typed_refusal(runner, tmp_path):
    tasks = _tasks_catalog(tmp_path, with_siblings=True)
    out = _run(runner, tasks, "hyp", "create", "an idea", "--json")
    assert out.exit_code == 1
    error = json.loads(out.output)["error"]
    assert error["code"] == "invalid_field"
    assert error["field"] == "hypothesis"


def test_hyp_append_verdict_and_autoclose_quorum(runner, tmp_path):
    tasks = _tasks_catalog(tmp_path, with_siblings=True)
    hid = _json(
        _run(runner, tasks, "hyp", "create", "h", "--hypothesis", "H", "--json")
    )["task"]["id"]
    # three distinct refuted sessions → quorum K=3 reached
    for sid in ("s1", "s2", "s3"):
        out = _run(
            runner, tasks, "hyp", "append-verdict", hid,
            "--verdict", "refuted", "--evidence", "no", "--session-id", sid, "--json",
        )
        assert out.exit_code == 0, out.output
    dry = _json(_run(runner, tasks, "hyp", "autoclose", "--dry-run", "--json"))
    assert dry["dry_run"] is True
    assert [c["hyp_id"] for c in dry["closures"]] == [hid]
    # real sweep closes it (refuted); a second run is idempotent (now closed)
    done = _json(_run(runner, tasks, "hyp", "autoclose", "--json"))
    assert [c["hyp_id"] for c in done["closures"]] == [hid]
    after = _json(_run(runner, tasks, "context", hid, "--json"))["task"]
    assert after["state"] == "refuted"
    again = _json(_run(runner, tasks, "hyp", "autoclose", "--json"))
    assert again["closures"] == []


def test_hyp_manual_refute_by_edge_name_then_update(runner, tmp_path):
    tasks = _tasks_catalog(tmp_path, with_siblings=True)
    hid = _json(
        _run(runner, tasks, "hyp", "create", "h", "--hypothesis", "H", "--json")
    )["task"]["id"]
    # edge-name sugar: `refute` resolves to active→refuted (never quorum-gated for a human)
    refuted = _json(_run(runner, tasks, "transition", hid, "refute", "--json"))
    assert refuted["task"]["state"] == "refuted"
    updated = _json(
        _run(runner, tasks, "hyp", "update", hid, "--observation_window", "2 weeks", "--json")
    )
    assert updated["task"]["observation_window"] == "2 weeks"


def test_hyp_revive_edge_name_from_stalled(runner, tmp_path):
    tasks = _tasks_catalog(tmp_path, with_siblings=True)
    hid = _json(
        _run(runner, tasks, "hyp", "create", "h", "--hypothesis", "H", "--json")
    )["task"]["id"]
    _run(runner, tasks, "transition", hid, "stall", "--json")
    revived = _json(_run(runner, tasks, "transition", hid, "revive", "--json"))
    assert revived["task"]["state"] == "active"


# ----- proposal flow (create → vote → accept) --------------------------------


def test_proposal_create_vote_accept(runner, tmp_path):
    tasks = _tasks_catalog(tmp_path, with_siblings=True)
    pid = _json(
        _run(
            runner, tasks, "proposal", "create", "a proposal",
            "--category", "rule", "--target", "t", "--description", "d", "--rationale", "why",
            "--json",
        )
    )["task"]["id"]
    assert pid == "PROP-001"
    voted = _json(
        _run(runner, tasks, "proposal", "vote", pid, "--reasoning", "sound", "--session-id", "s1", "--json")
    )
    assert voted["task"]["votes"][-1]["session_id"] == "s1"
    accepted = _json(_run(runner, tasks, "transition", pid, "accept", "--json"))
    assert accepted["task"]["state"] == "accepted"


def test_proposal_create_bad_choice_is_typed_refusal(runner, tmp_path):
    tasks = _tasks_catalog(tmp_path, with_siblings=True)
    out = _run(
        runner, tasks, "proposal", "create", "p",
        "--category", "nope", "--target", "t", "--description", "d", "--rationale", "why", "--json",
    )
    assert out.exit_code == 1
    error = json.loads(out.output)["error"]
    assert error["field"] == "category"
    assert "rule" in error["choices"]


def test_proposal_vote_missing_session_is_typed_refusal(runner, tmp_path):
    tasks = _tasks_catalog(tmp_path, with_siblings=True)
    pid = _json(
        _run(
            runner, tasks, "proposal", "create", "p",
            "--category", "rule", "--target", "t", "--description", "d", "--rationale", "why",
            "--json",
        )
    )["task"]["id"]
    # no --session-id and no ambient session → the prop-vote validator refuses
    out = _run(runner, tasks, "proposal", "vote", pid, "--reasoning", "sound", "--json")
    assert out.exit_code == 1
    assert json.loads(out.output)["error"]["field"] == "votes"
