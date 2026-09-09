"""e2e (HATS-294, HATS-709)

flow:   a developer invoking retired self clean CLI command
cmds:
    ai-hats self clean  # no-resolve: pins that this CLI was removed
expect: CLI exits with error code explaining self clean command is retired
why:    without self clean removal guard, deprecated self clean subcommand might be re-introduced"""

from __future__ import annotations

import subprocess

import pytest

pytestmark = pytest.mark.install


def _run(cmd, *, cwd, env, timeout=120):
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.mark.integration
def test_self_clean_command_removed(shared_launcher, tmp_path):
    """``ai-hats self clean`` no longer exists; ``self --help`` does not list it."""
    launcher, env, _venv = shared_launcher
    project = tmp_path / "project"
    project.mkdir()

    # The retired command → click usage error (non-zero, "No such command 'clean'").
    res = _run([str(launcher), "self", "clean"], cwd=project, env=env)
    assert res.returncode != 0, (
        "`ai-hats self clean` succeeded (exit 0) — the retired command is back?\n"
        f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
    combined = res.stdout + res.stderr
    assert "No such command" in combined and "clean" in combined, (
        f"expected click \"No such command 'clean'\" usage error:\n{combined}"
    )

    # And it must not be advertised as a subcommand of the `self` group.
    help_res = _run([str(launcher), "self", "--help"], cwd=project, env=env)
    assert help_res.returncode == 0, help_res.stderr
    listed = [
        ln.split()[0]
        for ln in help_res.stdout.splitlines()
        if ln.startswith("  ") and ln.strip() and not ln.strip().startswith("-")
    ]
    assert "clean" not in listed, f"`clean` still listed as a `self` subcommand:\n{help_res.stdout}"
