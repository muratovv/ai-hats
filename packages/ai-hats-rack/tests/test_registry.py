"""Link-kind registry: parse, projection, hierarchy binding, per-backlog override
(HATS-1028). Semantics (is_epic / epic-automation) bind from above to a
configured kind — proven here without a hardcoded ``parent_task``."""

from __future__ import annotations

import pytest
import yaml

from ai_hats_rack.kernel import Kernel
from ai_hats_rack.models import TaskCard
from ai_hats_rack.registry import (
    LinksRegistryError,
    UnknownLinkKindError,
    load_registry,
    load_registry_for,
    resolve_links,
)

RENAMED = """\
kinds:
  - {name: epic_of, legacy_field: parent_task, arity: one, inverse: subtasks_view}
  - {name: subtasks_view, derived: true, inverse: epic_of}
  - {name: depends_on, legacy_field: depends_on, aliases: [depends]}
  - {name: related, legacy_field: related, inverse: related}
"""


def _write(path, **fields):
    card = TaskCard(id=fields["id"], **{k: v for k, v in fields.items() if k != "id"})
    path.parent.mkdir(parents=True, exist_ok=True)
    card.save(path)


# ----- parsing / structure ---------------------------------------------------


def test_packaged_default_kinds():
    reg = load_registry()
    assert reg.names() == ("parent", "depends_on", "related", "children")
    assert reg.hierarchy_kind.name == "parent"
    assert reg.children_kind.name == "children"
    assert reg.get("depends_on").aliases == ("depends",)
    assert reg.require("depends").name == "depends_on"  # alias resolves
    assert reg.get("related").symmetric is True


def test_unknown_kind_names_the_configured_set():
    reg = load_registry()
    with pytest.raises(UnknownLinkKindError) as err:
        reg.require("blocks")
    assert err.value.configured == ("parent", "depends_on", "related", "children")


def test_dangling_inverse_is_rejected(tmp_path):
    bad = tmp_path / "links.yaml"
    bad.write_text("kinds:\n  - {name: parent, inverse: ghost}\n")
    with pytest.raises(LinksRegistryError):
        load_registry(bad)


def test_empty_kinds_is_rejected(tmp_path):
    bad = tmp_path / "links.yaml"
    bad.write_text("kinds: []\n")
    with pytest.raises(LinksRegistryError):
        load_registry(bad)


# ----- resolve_links projection ----------------------------------------------


def test_resolve_reads_legacy_and_generic_and_derived():
    reg = load_registry()
    card = TaskCard(
        id="T-1",
        parent_task="T-0",
        depends_on=["T-2"],
        related=["T-3"],
        links={"reviewed_with": ["T-9"]},  # not a configured kind → omitted
    )
    resolved = resolve_links(reg, card, derived={"children": ["T-4", "T-5"]})
    assert resolved == {
        "parent": ["T-0"],
        "depends_on": ["T-2"],
        "related": ["T-3"],
        "children": ["T-4", "T-5"],
    }


def test_resolve_is_byte_clean_for_empty_kinds():
    reg = load_registry()
    assert resolve_links(reg, TaskCard(id="T-1")) == {}


# ----- round-trip: legacy card ↔ links view, no loss / no duplication --------


def test_legacy_card_round_trips_without_migration(tmp_path):
    reg = load_registry()
    path = tmp_path / "T-1" / "task.yaml"
    _write(path, id="T-1", parent_task="T-0", depends_on=["T-2"], related=["T-3"])
    card = TaskCard.from_yaml(path)
    # the links view reads legacy fields as kinds ...
    assert resolve_links(reg, card) == {
        "parent": ["T-0"],
        "depends_on": ["T-2"],
        "related": ["T-3"],
    }
    # ... and saving does NOT migrate them into `links:` (no dup, no new key)
    card.save(path)
    raw = yaml.safe_load(path.read_text())
    assert raw["parent_task"] == "T-0" and raw["depends_on"] == ["T-2"]
    assert "links" not in raw


# ----- hierarchy binding: is_epic / children_of via a RENAMED parent kind ----


def test_is_epic_via_renamed_parent_kind(tmp_path, cwd):
    reg = load_registry(_registry_file(tmp_path))
    tasks = tmp_path / "tasks"
    kernel = Kernel(tasks, registry=reg)
    kernel.create(actor="t", caller_cwd=cwd, task_id="T-1", title="epic")
    kernel.create(actor="t", caller_cwd=cwd, task_id="T-2", parent_task="T-1")
    # the kind is named `epic_of`, not `parent`, yet is_epic still binds to it
    assert kernel.children_of("T-1") == ["T-2"]
    assert kernel.is_epic("T-1") is True
    assert kernel.is_epic("T-2") is False


def test_inverse_consistency_parent_children(tmp_path):
    reg = load_registry()
    tasks = tmp_path / "tasks"
    _write(tasks / "T-1" / "task.yaml", id="T-1")
    _write(tasks / "T-2" / "task.yaml", id="T-2", parent_task="T-1")
    kernel = Kernel(tasks, registry=reg)
    child = TaskCard.from_yaml(tasks / "T-2" / "task.yaml")
    # forward edge on the child and the derived reverse on the parent agree
    assert resolve_links(reg, child)["parent"] == ["T-1"]
    parent = TaskCard.from_yaml(tasks / "T-1" / "task.yaml")
    derived = {"children": kernel.children_of("T-1")}
    assert resolve_links(reg, parent, derived=derived)["children"] == ["T-2"]


def test_derived_children_are_not_stored(tmp_path, cwd):
    reg = load_registry()
    tasks = tmp_path / "tasks"
    kernel = Kernel(tasks, registry=reg)
    kernel.create(actor="t", caller_cwd=cwd, task_id="T-1", title="epic")
    kernel.create(actor="t", caller_cwd=cwd, task_id="T-2", parent_task="T-1")
    # children is a computed view — nothing about it is persisted on the parent
    raw = yaml.safe_load((tasks / "T-1" / "task.yaml").read_text())
    assert "children" not in raw
    assert "links" not in raw


# ----- binding point: any extension can consume a configured kind ------------


class _DependsProbe:
    """Stand-in for a future depends-blocker: bound to a kind by config, it
    reads that kind's ids via the same registry projection is_epic uses. Proves
    the binding point generalizes without implementing blocking (HATS-1028)."""

    def __init__(self, registry, kind="depends_on"):
        self._registry = registry
        self._kind = kind

    def blockers(self, card):
        self._registry.require(self._kind)  # config-validated binding
        return resolve_links(self._registry, card).get(self._kind, [])


def test_extension_binds_to_configured_kind():
    reg = load_registry()
    card = TaskCard(id="T-1", depends_on=["T-2", "T-3"])
    assert _DependsProbe(reg).blockers(card) == ["T-2", "T-3"]


def test_extension_binding_follows_a_renamed_kind(tmp_path):
    # Point the probe at a backlog whose depends kind keeps its name but whose
    # parent kind is renamed — the probe still consumes depends_on unchanged.
    reg = load_registry(_registry_file(tmp_path))
    card = TaskCard(id="T-1", depends_on=["T-9"])
    assert _DependsProbe(reg, "depends_on").blockers(card) == ["T-9"]


# ----- per-backlog override discovery ----------------------------------------


def test_load_registry_for_prefers_project_override(tmp_path):
    (tmp_path / "links.yaml").write_text(RENAMED)
    reg = load_registry_for(tmp_path)
    assert reg.hierarchy_kind.name == "epic_of"


def test_load_registry_for_falls_back_to_packaged_default(tmp_path):
    reg = load_registry_for(tmp_path)  # no project links.yaml
    assert reg.hierarchy_kind.name == "parent"


def _registry_file(tmp_path):
    path = tmp_path / "links.yaml"
    path.write_text(RENAMED)
    return path
