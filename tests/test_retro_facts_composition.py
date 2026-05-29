"""Tests for SessionFacts.composition parsing — HATS-442.

Covers:
- New sessions whose metrics.json includes `composition` → parsed.
- Old sessions without the field → composition is None (backwards compat).
- Missing metrics.json → composition is None.
- Malformed metrics.json → composition is None.
"""

import json
from pathlib import Path


from ai_hats.retro.facts import _parse_composition


def _make_session_dir(tmp_path: Path, metrics: dict | None) -> Path:
    """Build a session dir with optional metrics.json content."""
    sdir = tmp_path / "session_test"
    sdir.mkdir()
    if metrics is not None:
        (sdir / "metrics.json").write_text(json.dumps(metrics))
    return sdir


def test_missing_metrics_returns_none(tmp_path: Path):
    sdir = _make_session_dir(tmp_path, metrics=None)
    assert _parse_composition(sdir) is None


def test_metrics_without_composition_returns_none(tmp_path: Path):
    sdir = _make_session_dir(
        tmp_path,
        metrics={"role": "maintainer", "turns": 3, "tool_calls": 5},
    )
    assert _parse_composition(sdir) is None


def test_malformed_json_returns_none(tmp_path: Path):
    sdir = tmp_path / "session_bad"
    sdir.mkdir()
    (sdir / "metrics.json").write_text("{not valid json")
    assert _parse_composition(sdir) is None


def test_composition_with_provenance_parsed(tmp_path: Path):
    composition = {
        "traits": ["trait-base", "personal-workflow"],
        "rules": ["global_rule_destructive_actions"],
        "skills": [],
        "provenance": {
            "traits": {
                "trait-base": "built-in",
                "personal-workflow": "global",
            },
            "rules": {"global_rule_destructive_actions": "built-in"},
            "skills": {},
        },
    }
    sdir = _make_session_dir(
        tmp_path, metrics={"role": "maintainer", "composition": composition}
    )
    parsed = _parse_composition(sdir)
    assert parsed == composition


def test_composition_non_dict_returns_none(tmp_path: Path):
    """Defensive: if someone writes a corrupted composition field as a list,
    the parser falls back to None rather than letting downstream code crash."""
    sdir = _make_session_dir(
        tmp_path,
        metrics={"role": "maintainer", "composition": ["this", "is", "not", "a", "dict"]},
    )
    assert _parse_composition(sdir) is None
