"""Data-format compatibility pins: rack loads/round-trips tracker task.yaml."""

from __future__ import annotations

import pytest
import yaml

from ai_hats_rack.models import DeltaFieldError, TaskCard

OLD_CARD = """\
id: HATS-402
title: Old-format card
state: review
description: written by ai-hats-tracker
priority: high
assignee: ''
reviewer: user
role: maintainer
parent_task: HATS-400
subtasks: []
tags: [tracker]
work_log:
  - '2026-01-01: bare string entry (pre-WorkLogEntry format)'
  - timestamp: 2026-05-01T10:00:00Z
    message: structured entry
created: 2026-04-06
updated: '2026-05-01T10:00:00Z'
attachments:
  - name: evidence.log
    digest: 41abcdef0123
    added: '2026-05-01T10:00:00Z'
    note: keep
acceptance_criteria:
  - loads without loss
"""


def test_old_card_loads_with_defaults(tmp_path):
    path = tmp_path / "task.yaml"
    path.write_text(OLD_CARD)
    card = TaskCard.from_yaml(path)
    assert card.id == "HATS-402"
    assert card.state == "review"
    # bare-string work_log entries coerce instead of failing the load
    assert card.work_log[0].message.startswith("2026-01-01")
    assert card.work_log[1].message == "structured entry"
    # unquoted YAML date stays a string field
    assert card.created == "2026-04-06"
    # fields the rack does not type (attachments, acceptance_criteria) are
    # captured, not dropped
    assert card.extras["attachments"][0]["name"] == "evidence.log"
    assert card.extras["acceptance_criteria"] == ["loads without loss"]
    # new-in-rack unset fields default cleanly
    assert card.final_state == ""
    assert card.completed_at == ""


def test_unknown_fields_round_trip_on_save(tmp_path):
    src = tmp_path / "task.yaml"
    src.write_text(OLD_CARD)
    card = TaskCard.from_yaml(src)
    out = tmp_path / "saved.yaml"
    card.save(out)
    data = yaml.safe_load(out.read_text())
    assert data["attachments"][0]["digest"] == "41abcdef0123"
    assert data["acceptance_criteria"] == ["loads without loss"]
    assert data["state"] == "review"
    # a second load sees the same content
    again = TaskCard.from_yaml(out)
    assert again.extras == card.extras
    assert [e.message for e in again.work_log] == [e.message for e in card.work_log]


def test_empty_link_fields_not_emitted(tmp_path):
    card = TaskCard(id="T-1", title="clean")
    out = tmp_path / "task.yaml"
    card.save(out)
    data = yaml.safe_load(out.read_text())
    for noise in ("depends_on", "related", "see_also", "folded_into", "links", "resolution"):
        assert noise not in data


def test_generic_links_round_trip(tmp_path):
    # HATS-1028: new kinds live in the top-level `links:` key; empty kinds are
    # dropped (byte-clean), non-empty ones survive a save/load cycle verbatim.
    card = TaskCard(id="T-1", title="linked", links={"reviewed_with": ["T-9"], "blocks": []})
    out = tmp_path / "task.yaml"
    card.save(out)
    data = yaml.safe_load(out.read_text())
    assert data["links"] == {"reviewed_with": ["T-9"]}  # empty `blocks` not emitted
    again = TaskCard.from_yaml(out)
    assert again.links == {"reviewed_with": ["T-9"]}


def test_old_rack_card_reloads_links_from_extras(tmp_path):
    # A card that once carried `links` inside extras (e.g. saved by a kind-blind
    # reader) parses back into the typed field, not a duplicated extras key.
    path = tmp_path / "task.yaml"
    path.write_text("id: T-1\ntitle: t\nlinks:\n  reviewed_with: [T-9]\n")
    card = TaskCard.from_yaml(path)
    assert card.links == {"reviewed_with": ["T-9"]}
    assert "links" not in card.extras


def test_work_policy_round_trips_as_typed_field(tmp_path):
    # HATS-1067: work_policy is a typed column (not extras) and survives a
    # save/load cycle verbatim — multi-line markdown body included.
    body = "## Requirements for children\n- respect the public API\n- no new deps"
    card = TaskCard(id="T-1", title="epic", work_policy=body)
    out = tmp_path / "task.yaml"
    card.save(out)
    data = yaml.safe_load(out.read_text())
    assert data["work_policy"] == body  # typed key, not parked under extras
    again = TaskCard.from_yaml(out)
    assert again.work_policy == body
    assert "work_policy" not in again.extras


def test_empty_work_policy_not_emitted(tmp_path):
    # emit: when-set — an unset work_policy leaves no key (byte-clean first save).
    card = TaskCard(id="T-1", title="clean")
    out = tmp_path / "task.yaml"
    card.save(out)
    assert "work_policy" not in yaml.safe_load(out.read_text())


def test_log_work_actor_prefix():
    card = TaskCard(id="T-1")
    card.log_work("plain")
    card.log_work("attributed", actor="session:abc")
    assert card.work_log[0].message == "plain"
    assert card.work_log[1].message == "[session:abc] attributed"
    assert card.work_log[1].timestamp  # stamped


# ----- Delta.fields ops (set_field / append_field, HATS-1043) -----------------


def test_set_field_typed_scalar_and_unknown_extras():
    card = TaskCard(id="T-1")
    card.set_field("priority", "high")  # typed str field
    card.set_field("validation_log", [{"v": "ok"}])  # unknown key → extras
    assert card.priority == "high"
    assert card.extras["validation_log"] == [{"v": "ok"}]


def test_set_field_type_mismatch_raises():
    card = TaskCard(id="T-1")
    with pytest.raises(DeltaFieldError, match="Set expects str"):
        card.set_field("priority", 5)


def test_append_field_typed_list_and_unknown_extras():
    card = TaskCard(id="T-1", tags=["a"])
    card.append_field("tags", "b")  # typed list field
    card.append_field("votes", {"by": "x"})  # unknown key → extras list (created)
    assert card.tags == ["a", "b"]
    assert card.extras["votes"] == [{"by": "x"}]


def test_append_field_onto_scalar_raises():
    card = TaskCard(id="T-1")
    with pytest.raises(DeltaFieldError, match="Append requires a list field"):
        card.append_field("priority", "x")


def test_appended_extras_field_round_trips(tmp_path):
    card = TaskCard(id="T-1", title="t")
    card.append_field("validation_log", {"verdict": "pass"})
    out = tmp_path / "task.yaml"
    card.save(out)
    again = TaskCard.from_yaml(out)
    assert again.extras["validation_log"] == [{"verdict": "pass"}]
