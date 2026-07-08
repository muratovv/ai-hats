"""HATS-952 (T15/0.2.0) — wt-free defaults of the session-CLI injection seam.

The observe session CLI (`list`/`show`/`audit`) defaults to project-local,
worktree-free resolvers so it runs with only ai-hats-core; the integrator
overrides the ``_seam`` module globals at mount. This pins the standalone
defaults' behaviour (the counterpart of the tracker's ``_seam`` defaults).
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_default_runs_dir_is_project_local_agent_subtree(tmp_path: Path) -> None:
    from ai_hats_observe.cli import _seam

    assert _seam._default_runs_dir(tmp_path) == tmp_path / ".agent" / "sessions" / "runs"


def test_default_project_dir_prefers_agent_holder(tmp_path: Path, monkeypatch) -> None:
    from ai_hats_observe.cli import _seam

    (tmp_path / ".agent").mkdir()
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    assert _seam._default_project_dir() == tmp_path


def test_default_tag_filter_parser_splits_kv() -> None:
    from ai_hats_observe.cli import _seam

    assert _seam._default_tag_filter_parser(["env=prod", "tier=gold"]) == {
        "env": "prod",
        "tier": "gold",
    }


def test_default_tag_filter_parser_rejects_malformed() -> None:
    from ai_hats_observe.cli import _seam

    with pytest.raises(ValueError):
        _seam._default_tag_filter_parser(["noequals"])


def test_seam_slots_default_to_the_wt_free_functions() -> None:
    from ai_hats_observe.cli import _seam

    assert _seam._PROJECT_DIR is _seam._default_project_dir
    assert _seam._RUNS_DIR is _seam._default_runs_dir
    assert _seam._TAG_FILTER_PARSER is _seam._default_tag_filter_parser
