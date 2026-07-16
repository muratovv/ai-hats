"""E2E: an out-of-tree skill source on the ``ai_hats.skills`` entry-point group
is discovered by the real ``ai-hats`` binary (HATS-871 / T11).

Builds a synthetic installed distribution — an anchor package holding a
``skills/<name>/SKILL.md`` data dir plus a ``*.dist-info`` carrying
``entry_points.txt`` — on ``PYTHONPATH`` and drives ``ai-hats list skills``. The
plugin's skill appears alongside the built-ins WITHOUT ai-hats hard-coding the
package (IoC via ``importlib.metadata``). Fail-under-revert: drop
``_load_skill_source_entry_points`` → ``acme-skill`` never resolves → assertion fails.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from _helpers.env import checkout_pythonpath


pytestmark = pytest.mark.integration


def _write_skill_plugin_dist(root: Path) -> Path:
    """A synthetic installed dist advertising a skill source anchor."""
    root.mkdir(parents=True, exist_ok=True)
    pkg = root / "acme_skills_pkg"
    (pkg).mkdir()
    (pkg / "__init__.py").write_text("")  # thin data anchor
    skill_dir = pkg / "skills" / "acme-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: acme-skill\ndescription: out-of-tree skill\n---\nbody\n"
    )
    dist_info = root / "acme_hats_skills-0.1.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: acme-hats-skills\nVersion: 0.1\n"
    )
    (dist_info / "entry_points.txt").write_text(
        "[ai_hats.skills]\nacme = acme_skills_pkg\n"
    )
    return root


def test_out_of_tree_skill_source_is_discovered_by_the_binary(
    ai_hats_shim: Path, repo_root: Path, tmp_path: Path
):
    plugin_dir = _write_skill_plugin_dist(tmp_path / "plugin")

    env = os.environ.copy()  # PYTHONPATH already scrubbed by _scrub_redirect_env
    env["PYTHONPATH"] = checkout_pythonpath(repo_root) + os.pathsep + str(plugin_dir)

    result = subprocess.run(
        [str(ai_hats_shim), "list", "skills"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "acme-skill" in result.stdout, result.stdout
    # discovery augments, not replaces — the built-in library skill is still there
    assert "backlog-manager" in result.stdout, result.stdout
