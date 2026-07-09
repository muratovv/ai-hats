"""HATS-952 (T15/0.2.0) — wt-free defaults of the session-CLI injection seam.

The observe session CLI (`list`/`show`/`audit`) defaults to project-local,
worktree-free resolvers so it runs with only ai-hats-core; the integrator
overrides the ``_seam`` module globals at mount. This pins the standalone
defaults' behaviour (the counterpart of the tracker's ``_seam`` defaults).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]


def test_default_runs_dir_is_project_local_agent_subtree(tmp_path: Path) -> None:
    from ai_hats_observe.cli import _seam

    assert _seam._default_runs_dir(tmp_path) == tmp_path / ".agent" / "sessions" / "runs"


def test_project_dir_default_is_the_shared_core_resolver() -> None:
    """The wt-free project-dir default delegates to ``ai_hats_core`` (HATS-952) —
    the walk-up behaviour itself is covered by core's ``test_paths.py``."""
    from ai_hats_core.paths import default_project_dir

    from ai_hats_observe.cli import _seam

    assert _seam._default_project_dir is default_project_dir


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


def test_seam_slots_default_to_wt_free_functions_on_fresh_import() -> None:
    """A fresh import of the seam wires the slots to their wt-free defaults and
    pulls no ``ai_hats`` integrator. Runs in a clean subprocess: the integrator
    override mutates the shared ``_seam`` globals process-wide, so an in-process
    identity assert is contaminated by any earlier ``import ai_hats.cli``.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(_WORKSPACE_ROOT / "packages" / "ai-hats-observe" / "src"),
            str(_WORKSPACE_ROOT / "packages" / "ai-hats-core" / "src"),
            env.get("PYTHONPATH", ""),
        ]
    )
    code = (
        "import sys\n"
        "import ai_hats_observe.cli._seam as s\n"
        "assert s._PROJECT_DIR is s._default_project_dir\n"
        "assert s._RUNS_DIR is s._default_runs_dir\n"
        "assert s._TAG_FILTER_PARSER is s._default_tag_filter_parser\n"
        "assert 'ai_hats' not in sys.modules, 'seam import pulled the integrator'\n"
    )
    result = subprocess.run(  # noqa: S603 — fixed argv, our own interpreter
        [sys.executable, "-c", code], capture_output=True, text=True, env=env
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
