"""E2E: ``ai-hats self clean`` is retired (HATS-709).

The ``clean`` subsystem was a total no-op on v4 — framework content is
composed in memory (HATS-294), and the only materialized managed content
(``library/hooks``) is owned by ``_refresh``. The command plus its dead
helper chain (``_clean`` / ``_clean_non_local`` / ``_clean_managed_entries``
/ ``_write_managed_manifest``) were removed.

Per ``dev_rule_e2e_gate``, the ``src/ai_hats/cli/`` surface change (command
+ registration removed) needs a **real-subprocess** test that fails if the
command is restored. Runs the real launcher + real pip + real ``ai-hats``
binary (session-shared venv via ``shared_launcher``). Marked ``integration``.

Fail-under-revert: re-add ``self_group.add_command(assembly.clean)`` in
``cli/__init__.py`` (and the ``clean()`` command in ``cli/assembly.py``) and
``ai-hats self clean`` exits 0 again → this test fails.
"""

from __future__ import annotations

import subprocess

import pytest


def _run(cmd, *, cwd, env, timeout=120):
    return subprocess.run(
        cmd, cwd=str(cwd), env=env,
        capture_output=True, text=True, timeout=timeout,
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
    assert "clean" not in listed, (
        f"`clean` still listed as a `self` subcommand:\n{help_res.stdout}"
    )
