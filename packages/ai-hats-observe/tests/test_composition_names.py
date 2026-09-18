"""``composition_names`` — the one reader of a session's composition record.

Every consumer that lists what a session loaded (audit.md, the reviewer's
prompt) goes through it, so the record's shape has one place to change.
"""

from __future__ import annotations

from ai_hats_observe import composition_names, snapshot_names, stored_composition_names


def _record() -> dict:
    return {
        "identity": "maintainer",
        "prompt": {
            "blocks": [
                {"name": None, "members": [{"name": "trait-base::prompt"}]},
                {
                    "name": "RULES",
                    "members": [
                        {"name": "rules::rule_a"},
                        {"name": "rules::rule_b"},
                    ],
                },
            ]
        },
        "skills": [{"name": "skills::hatrack", "path": "/lib/skills/hatrack"}],
        "trace": [
            {"term": "trait-base", "brought_by": "maintainer", "removed_by": None},
            {"term": "dev::shell", "brought_by": "maintainer", "removed_by": "expr"},
            {"term": "rules::rule_a", "brought_by": "trait-base", "removed_by": None},
            {"term": "skills::hatrack", "brought_by": "overrides::project", "removed_by": None},
        ],
    }


def test_lists_traits_rules_and_skills_each_with_what_brought_it():
    names = composition_names(_record())
    assert names.traits == (("trait-base", "maintainer"),)
    assert names.rules == (("rule_a", "trait-base"), ("rule_b", "expression"))
    assert names.skills == (("hatrack", "overrides::project"),)


def _snapshot() -> dict:
    return {
        "traits": ["trait-base", "personal-workflow"],
        "rules": ["rule_a"],
        "skills": ["hatrack"],
        "provenance": {
            "traits": {"trait-base": "built-in", "personal-workflow": "global"},
            "rules": {"rule_a": "project"},
            "skills": {},
        },
    }


def test_snapshot_names_tag_each_name_by_its_provenance_layer():
    names = snapshot_names(_snapshot())
    assert names.traits == (("trait-base", "built-in"), ("personal-workflow", "global"))
    assert names.rules == (("rule_a", "project"),)
    assert names.skills == (("hatrack", "built-in"),)


def test_stored_composition_names_routes_by_shape():
    assert stored_composition_names(_record()) == composition_names(_record())
    assert stored_composition_names(_snapshot()) == snapshot_names(_snapshot())
