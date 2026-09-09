"""The standalone host of the session CLI.

The observe session CLI (`list`/`show`/`audit`) runs under ``STANDALONE`` —
project-local, worktree-free resolvers — so it works with only ai-hats-core; an
integrator attaches its own ``Host``. This pins the standalone behaviour and that
a fresh import runs under it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]


def test_layout_default_is_the_shared_core_resolver(tmp_path: Path) -> None:
    """The wt-free layout default delegates to ``ai_hats_core`` (HATS-1606) —
    the walk-up behaviour itself is covered by core's ``test_layout.py``.
    Standalone keeps the deliberate flat tree: base is ``.agent``, not
    ``.agent/ai-hats``."""
    from ai_hats_observe.cli import _host

    (tmp_path / ".agent").mkdir()
    layout = _host._default_layout(tmp_path)
    assert layout.root == tmp_path.resolve()
    assert layout.sessions.runs == tmp_path.resolve() / ".agent" / "sessions" / "runs"


def test_default_tag_filter_parser_splits_kv() -> None:
    from ai_hats_observe.cli import _host

    assert _host._default_tag_filter_parser(["env=prod", "tier=gold"]) == {
        "env": "prod",
        "tier": "gold",
    }


def test_default_tag_filter_parser_rejects_malformed() -> None:
    from ai_hats_observe.cli import _host

    with pytest.raises(ValueError):
        _host._default_tag_filter_parser(["noequals"])


def test_fresh_import_runs_under_the_standalone_host() -> None:
    """A fresh import runs under ``STANDALONE`` and pulls no ``ai_hats`` integrator.

    A clean subprocess: an integrator attaches its host process-wide, so an
    in-process identity assert is contaminated by any earlier ``import ai_hats.cli``.
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
        "from ai_hats_observe.cli import STANDALONE, host\n"
        "assert host() is STANDALONE\n"
        "from ai_hats_observe.cli import _host as h\n"
        "assert STANDALONE.layout is h._default_layout\n"
        "assert 'ai_hats' not in sys.modules, 'seam import pulled the integrator'\n"
    )
    result = subprocess.run(  # noqa: S603 — fixed argv, our own interpreter
        [sys.executable, "-c", code], capture_output=True, text=True, env=env
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
