"""E2E: an out-of-tree provider on the ``ai_hats.providers`` entry-point group
is discovered by the real ``ai-hats`` binary (HATS-870 / T10).

Builds a synthetic installed distribution — a ``Provider`` subclass module plus
a ``*.dist-info`` carrying ``entry_points.txt`` — on ``PYTHONPATH`` and drives
``ai-hats list providers``. The plugin appears alongside the built-ins WITHOUT
ai-hats importing its package (IoC via ``importlib.metadata``). Fail-under-revert:
drop ``_load_provider_entry_points`` → ``acme`` never registers → assertion fails.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from _helpers.env import checkout_pythonpath


pytestmark = pytest.mark.integration


_PLUGIN_SRC = '''\
from pathlib import Path

from ai_hats.providers import Provider


class AcmeProvider(Provider):
    @property
    def name(self) -> str:
        return "acme"

    def system_prompt_path(self, project_dir: Path) -> Path:
        return project_dir / "ACME.md"

    def rules_dir(self, session_dir: Path) -> Path:
        return session_dir / "rules"

    def build_system_prompt(self, result) -> str:
        return "acme"

    def get_cli_command(self, args=None):
        return ["acme-cli", *(args or [])]

    def get_env(self, session_dir, project_dir):
        return {}
'''


def _write_plugin_dist(root: Path) -> Path:
    """A synthetic installed dist advertising a provider entry point."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "acme_provider.py").write_text(_PLUGIN_SRC)
    dist_info = root / "acme_hats-0.1.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: acme-hats\nVersion: 0.1\n"
    )
    (dist_info / "entry_points.txt").write_text(
        "[ai_hats.providers]\nacme = acme_provider:AcmeProvider\n"
    )
    return root


def test_out_of_tree_provider_is_discovered_by_the_binary(
    ai_hats_shim: Path, repo_root: Path, tmp_path: Path
):
    plugin_dir = _write_plugin_dist(tmp_path / "plugin")

    env = os.environ.copy()  # PYTHONPATH already scrubbed by _scrub_redirect_env
    env["PYTHONPATH"] = checkout_pythonpath(repo_root) + os.pathsep + str(plugin_dir)

    result = subprocess.run(
        [str(ai_hats_shim), "list", "providers"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "acme" in result.stdout, result.stdout
    # discovery augments, not replaces — the built-ins are still there
    assert "claude" in result.stdout, result.stdout
    assert "gemini" in result.stdout, result.stdout
