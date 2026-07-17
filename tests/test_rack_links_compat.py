"""Data-compat pins for the rack links registry (HATS-1028): a rack card with a
new link kind stays readable by the OLD tracker model, and a legacy tracker card
projects onto the links view with no migration. Bridges both packages, so it
lives on the integrator side (the rack suite stays first-party-free)."""

from __future__ import annotations

import pytest
import yaml

from ai_hats_rack.models import TaskCard as RackCard
from ai_hats_rack.registry import load_registry, resolve_links
from ai_hats_tracker.models import TaskCard as TrackerCard

pytestmark = pytest.mark.integration


def test_rack_new_kind_survives_old_tracker_round_trip(tmp_path):
    # A rack card writes a genuinely new kind into the top-level `links:` key.
    path = tmp_path / "task.yaml"
    RackCard(
        id="HATS-1",
        title="linked",
        state="review",
        parent_task="HATS-0",
        depends_on=["HATS-2"],
        links={"reviewed_with": ["HATS-9"]},
    ).save(path)

    # The old tracker model — which has never heard of `links` — reads the card
    # with zero loss: legacy fields typed, `links` captured verbatim in extras.
    old = TrackerCard.from_yaml(path)
    assert old.parent_task == "HATS-0"
    assert old.depends_on == ["HATS-2"]
    assert old.extras["links"] == {"reviewed_with": ["HATS-9"]}

    # And a save by the old model re-emits `links` byte-for-byte (round-trip).
    out = tmp_path / "resaved.yaml"
    old.save(out)
    assert yaml.safe_load(out.read_text())["links"] == {"reviewed_with": ["HATS-9"]}


def test_legacy_tracker_card_projects_onto_links_view(tmp_path):
    # A card written by the old tracker (no `links:` key) reads back through the
    # rack registry as kinds — legacy fields mapped, nothing migrated on disk.
    path = tmp_path / "task.yaml"
    TrackerCard(
        id="HATS-1", title="legacy", parent_task="HATS-0", depends_on=["HATS-2"], related=["HATS-3"]
    ).save(path)

    card = RackCard.from_yaml(path)
    assert resolve_links(load_registry(), card) == {
        "parent": ["HATS-0"],
        "depends_on": ["HATS-2"],
        "related": ["HATS-3"],
    }
    # a rack save keeps the legacy fields; it never invents a `links:` key
    resaved = tmp_path / "resaved.yaml"
    card.save(resaved)
    raw = yaml.safe_load(resaved.read_text())
    assert raw["parent_task"] == "HATS-0" and "links" not in raw
