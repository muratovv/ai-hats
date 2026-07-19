"""HATS-1035 step 7 smoke: the `rack` CLI over a temp catalog whose backlog.yaml
declares a custom schema (a custom `choices` set + an `emit: when-set` field).

Proves create / transition / context read the CATALOG's schema, not a hardcoded
one: create enforces the custom choices, a when-set field is absent until a
transition stamps it, and context reflects the schema throughout.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from ai_hats_rack.cli import main

_BACKLOG = """\
name: tasks
prefix: HATS
fsm:
  initial: brainstorm
  states:
    - { name: brainstorm }
    - { name: plan }
    - { name: execute }
    - { name: document }
    - { name: review }
    - { name: done, on_enter: [{ name: stamp-lifecycle, field: closed_at }] }
    - { name: blocked }
    - { name: cancelled }
  edges:
    - { from: brainstorm, to: plan }
    - { from: brainstorm, to: cancelled }
    - { from: plan, to: execute }
    - { from: execute, to: document }
    - { from: document, to: review }
    - { from: review, to: done }
    - { from: brainstorm, to: blocked }
    - { from: blocked, to: brainstorm }
links:
  kinds:
    - { name: parent_task, arity: one }
fields:
  - { name: priority, type: str, default: p2, choices: [p0, p1, p2] }
  - { name: closed_at, type: str, default: "", emit: when-set }
"""


@pytest.fixture
def catalog(tmp_path):
    tasks = tmp_path / "tasks"
    tasks.mkdir(parents=True)
    (tasks / "backlog.yaml").write_text(_BACKLOG, encoding="utf-8")
    return tasks


def _run(runner, catalog, *args):
    return runner.invoke(main, [*args, "--tasks-dir", str(catalog), "--json"])


def test_create_uses_the_catalog_choices(catalog):
    runner = CliRunner()
    ok = _run(runner, catalog, "create", "custom card", "--priority", "p0")
    assert ok.exit_code == 0, ok.output
    payload = json.loads(ok.output)["task"]
    assert payload["priority"] == "p0"  # the catalog's choice, not the packaged set
    assert "closed_at" not in payload  # emit: when-set — absent, unstamped

    bad = _run(runner, catalog, "create", "bad card", "--priority", "p9")
    assert bad.exit_code == 1
    error = json.loads(bad.output)["error"]
    assert error["field"] == "priority"
    assert error["choices"] == ["p0", "p1", "p2"]  # the CUSTOM choices are enforced


def test_create_default_is_the_catalog_default(catalog):
    runner = CliRunner()
    payload = json.loads(_run(runner, catalog, "create", "c").output)["task"]
    assert payload["priority"] == "p2"  # schema default, not the packaged "medium"


def test_transition_stamps_the_when_set_field_and_context_reflects_it(catalog):
    runner = CliRunner()
    created = json.loads(_run(runner, catalog, "create", "c").output)["task"]
    tid = created["id"]

    before = json.loads(_run(runner, catalog, "context", tid).output)["task"]
    assert "closed_at" not in before  # when-set, unstamped

    moved = _run(runner, catalog, "transition", tid, "done", "--force", "--reason", "smoke")
    assert moved.exit_code == 0, moved.output

    after = json.loads(_run(runner, catalog, "context", tid).output)["task"]
    assert after["state"] == "done"
    assert after["closed_at"]  # stamp-lifecycle {field: closed_at} wrote it — now emitted

    # the persisted card carries the stamped key and no empty when-set noise.
    text = (catalog / tid / "task.yaml").read_text(encoding="utf-8")
    assert "closed_at:" in text
