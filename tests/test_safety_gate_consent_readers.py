"""What the stdlib guard reads out of the launch-frozen declaration (HATS-1790).

The guard cannot import our parser, so the envelope carries the ends of a rack
selector ALREADY parsed and the guard reads the field (HATS-1719). Cutting the
name here is a second copy of a grammar that has changed before — and when it
changed, the copy went quiet instead of red.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_HOOKS = (
    Path(__file__).resolve().parents[1]
    / "packages/ai-hats-library/src/ai_hats_library/core/skills/safety-guard/hooks"
)


@pytest.fixture(scope="module")
def gate():
    """The guard, loaded the way the surface loads it — by path, stdlib only."""
    sys.path.insert(0, str(_HOOKS))
    try:
        spec = importlib.util.spec_from_file_location(
            "safety_gate_under_test", _HOOKS / "safety_gate.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(_HOOKS))


@pytest.fixture
def declared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def _write(rows: list[dict]) -> None:
        session_dir = tmp_path / "session"
        session_dir.mkdir(exist_ok=True)
        (session_dir / "role_materialization.json").write_text(
            json.dumps({"consent": rows}), encoding="utf-8"
        )
        monkeypatch.setenv(
            "AI_HATS_SESSION_IDENTITY", json.dumps({"session_dir": str(session_dir)})
        )

    return _write


def test_the_target_comes_from_the_parsed_field_and_not_from_a_cut(gate, declared):
    """The two answers are made to DIFFER, because agreeing proves nothing.

    A guard that cuts `selector` answers `'wrong'`; one that reads the field the
    composition parsed answers `'done'`. Only the second keeps HATS-1719's
    contract, and only a row where the two disagree can tell them apart.
    """
    declared(
        [
            {
                "app": "consent_gate",
                "path": ["rack.transition"],
                "selector": "x->wrong",
                "from": "x",
                "to": "done",
            }
        ]
    )

    assert gate.declared_consent_targets() == frozenset({"done"})


def test_a_row_the_composition_could_not_parse_names_no_target(gate, declared):
    """`selector_ends` answers `(None, None)` for anything that is not a rack
    selector, so an absent `to` is the composition saying "no ends here" — not an
    invitation to cut the name and guess."""
    declared(
        [
            {
                "app": "consent_gate",
                "path": ["wt.merge"],
                "selector": "pre-merge",
                "from": None,
                "to": None,
            }
        ]
    )

    assert gate.declared_consent_targets() == frozenset()
