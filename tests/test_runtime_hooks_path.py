"""HATS-412: lifecycle hooks must fire from the canonical hooks dir.

Two angles:

1. ``WrapRunner._make_session_hooks_runner`` returns a HooksRunner pointed
   at ``paths.hooks_dir()`` — contract test catching any future drift back
   to the legacy ``.agent/hooks/`` path.
2. ``HooksRunner.run`` against a real script under the canonical path
   actually executes it — integration test proving the chain end-to-end.

The two together replace the silent failure mode in production where
``HooksRunner`` was instantiated against an empty legacy directory
(``runtime.py:586`` pre-fix), causing every session_start hook to
return [] without warning.
"""

from __future__ import annotations

import stat
import subprocess

import pytest

from ai_hats.models import LifecycleEvent
from ai_hats.paths import hooks_dir as canonical_hooks_dir
from ai_hats.runtime import HooksRunner, WrapRunner


# ----- Helpers -----


def _seed_minimal_project(project_dir):
    """A WrapRunner-loadable project: ai-hats.yaml + canonical dir tree."""
    (project_dir / "ai-hats.yaml").write_text(
        "schema_version: 4\n"
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
    )
    (project_dir / ".agent" / "ai-hats").mkdir(parents=True)


# ----- 1. Contract -----


def test_wrap_runner_session_hooks_runner_uses_canonical_dir(tmp_path):
    """WrapRunner.run constructs HooksRunner at paths.hooks_dir()."""
    _seed_minimal_project(tmp_path)
    runner = WrapRunner(tmp_path)

    hooks_runner = runner._make_session_hooks_runner()

    assert isinstance(hooks_runner, HooksRunner)
    assert hooks_runner.hooks_dir == canonical_hooks_dir(tmp_path)
    # The legacy v0.6 path must NOT be used — that's the regression we fixed.
    assert hooks_runner.hooks_dir != tmp_path / ".agent" / "hooks"


def test_wrap_runner_session_hooks_runner_carries_project_dir(tmp_path):
    """The project_dir is threaded so hook scripts get $CLAUDE_PROJECT_DIR."""
    _seed_minimal_project(tmp_path)
    runner = WrapRunner(tmp_path)

    hooks_runner = runner._make_session_hooks_runner()

    assert hooks_runner.project_dir == tmp_path


# ----- 2. Integration: hook actually fires -----


@pytest.mark.skipif(
    not subprocess.run(["bash", "-c", "true"], check=False).returncode == 0,
    reason="bash unavailable",
)
def test_hook_script_under_canonical_dir_fires_on_session_start(tmp_path):
    """End-to-end: drop a script at <canonical_hooks>/session_start.sh,
    instantiate HooksRunner the same way WrapRunner does, run
    SESSION_START, assert sentinel file appears.

    Locks the full chain: canonical path resolution + HooksRunner script
    discovery (event-name prefix convention at runtime.py:188) + actual
    bash execution.
    """
    _seed_minimal_project(tmp_path)
    hooks_root = canonical_hooks_dir(tmp_path)
    hooks_root.mkdir(parents=True, exist_ok=True)
    sentinel = tmp_path / "hook_fired.txt"
    script = hooks_root / "session_start.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f"echo HATS-412 > '{sentinel}'\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    runner = WrapRunner(tmp_path)
    hooks_runner = runner._make_session_hooks_runner()
    results = hooks_runner.run(LifecycleEvent.SESSION_START, env={})

    assert sentinel.exists(), (
        f"hook script did not fire — runner.hooks_dir={hooks_runner.hooks_dir}, "
        f"results={results}"
    )
    assert sentinel.read_text().strip() == "HATS-412"
    assert len(results) == 1


def test_hook_under_legacy_path_does_not_fire(tmp_path):
    """A script dropped at the legacy ``.agent/hooks/`` path does NOT fire —
    proves the fix doesn't accidentally fall back to dual-path lookup
    (which would re-introduce the silent-discovery surface)."""
    _seed_minimal_project(tmp_path)
    legacy_dir = tmp_path / ".agent" / "hooks"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    sentinel = tmp_path / "legacy_hook_fired.txt"
    script = legacy_dir / "session_start.sh"
    script.write_text(
        f"#!/usr/bin/env bash\necho legacy > '{sentinel}'\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    runner = WrapRunner(tmp_path)
    hooks_runner = runner._make_session_hooks_runner()
    hooks_runner.run(LifecycleEvent.SESSION_START, env={})

    assert not sentinel.exists(), (
        "legacy .agent/hooks/ script fired — runtime is reading from both "
        "paths, which would re-introduce HATS-412's silent-discovery hazard"
    )
