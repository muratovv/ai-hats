"""E2E: ``ai-hats config set --channel`` persists the harness block and
``config status`` shows the Channel line (HATS-764).

Per ``dev_rule_e2e_gate``, the ``src/ai_hats/cli/`` surface change (new
``--channel/--repo/--path`` flags + a Channel status line) needs a
real-subprocess test: real launcher + real ``ai-hats`` binary (session-shared
venv via ``shared_launcher``), marked ``integration``.

Fail-under-revert: the pre-HATS-764 ``config set`` has no ``--channel`` option →
the invocation is a click "no such option" usage error (non-zero), so the
exit-0 + yaml-round-trip assertions below fail.
"""

from __future__ import annotations

import subprocess

import pytest
import yaml
from ai_hats.paths import PROJECT_CONFIG


def _run(cmd, *, cwd, env, timeout=120):
    return subprocess.run(
        cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout,
    )


@pytest.mark.integration
def test_e2e_config_set_channel_roundtrips(shared_launcher, tmp_path):
    launcher, env, _venv = shared_launcher
    project = tmp_path / "project"
    project.mkdir()
    yaml_path = project / PROJECT_CONFIG

    # Init the project (auto-init via provider-only set).
    init = _run([str(launcher), "config", "set", "-p", "claude"], cwd=project, env=env)
    assert init.returncode == 0, f"init failed:\n{init.stdout}\n{init.stderr}"

    # edge → harness block written; status shows the Channel line.
    edge = _run([str(launcher), "config", "set", "--channel", "edge"], cwd=project, env=env)
    assert edge.returncode == 0, f"set --channel edge failed:\n{edge.stdout}\n{edge.stderr}"
    assert yaml.safe_load(yaml_path.read_text())["harness"] == {"channel": "edge"}

    status = _run([str(launcher), "config", "status"], cwd=project, env=env)
    assert status.returncode == 0, status.stderr
    assert "Channel:" in status.stdout and "edge" in status.stdout

    # local + path round-trips.
    loc = _run(
        [str(launcher), "config", "set", "--channel", "local", "--path", "."],
        cwd=project, env=env,
    )
    assert loc.returncode == 0, f"set --channel local failed:\n{loc.stdout}\n{loc.stderr}"
    assert yaml.safe_load(yaml_path.read_text())["harness"] == {"channel": "local", "path": "."}

    # stable is the default → omitted from yaml (byte-clean, no harness: block).
    stable = _run([str(launcher), "config", "set", "--channel", "stable"], cwd=project, env=env)
    assert stable.returncode == 0, f"set --channel stable failed:\n{stable.stdout}\n{stable.stderr}"
    assert "harness" not in yaml_path.read_text()

    # --repo is edge-only → rejected with a non-zero exit on a non-edge channel.
    bad = _run(
        [str(launcher), "config", "set", "--channel", "local", "--repo", "https://x/y.git"],
        cwd=project, env=env,
    )
    assert bad.returncode != 0, "expected --repo on a non-edge channel to fail"
    assert "--repo is only valid with --channel edge" in (bad.stdout + bad.stderr)
