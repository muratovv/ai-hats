"""The single real-subprocess wiring sweep for the ``ai-hats task`` surface family.

`dev_rule_e2e_gate §2` requires *a* real-subprocess test per CLI surface — a
**wiring** check (the command resolves through the real binary; correct exit
code; one stable output marker), not a full semantic re-run. The semantics
(YAML roundtrip, link-body rendering, resolution text, required-flag rejection,
state-after-force) are owned by the in-process CliRunner unit suites and are
NOT re-asserted here:

- `task close / link / unlink / transition --force`  → `tests/test_cli_task.py`
- `task show <linked>` default vs `--short`           → `tests/test_cli_task_show_linked.py`
- `task hyp create --verification-protocol`           → `tests/test_cli_hyp.py`

Precedent (HATS-745): **one wiring sweep per CLI surface family**, exit-code +
marker only. `task`, `task hyp`, and `task show` all live under `ai-hats task`,
so they share this one sweep instead of multiplying a micro-e2e file per
incident — the growth pattern that pushed the gate 146→201 tests in two weeks.
Folded in from the now-deleted `test_hyp_create_e2e.py` (HATS-623) and
`test_task_show_linked_e2e.py` (HATS-691).

Runs the **real** launcher + **real** pip install + **real** ai-hats binary
(``shared_launcher`` tier — the only tier that satisfies §2's "real pip
install"). Slow (~60s on a warm pip cache). Marked ``integration``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _run(cmd, *, cwd, env, timeout, expect_exit=0):
    result = subprocess.run(
        cmd, cwd=str(cwd), env=env,
        capture_output=True, text=True, timeout=timeout,
    )
    if expect_exit is not None and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


@pytest.mark.integration
def test_e2e_task_surface_wiring_sweep(shared_launcher, tmp_path):
    """Wiring sweep for the whole ``ai-hats task`` surface, one real-binary chain.

    Each step asserts only the subprocess-boundary contract: the command
    resolves and exits as expected, plus a stable output marker where the exit
    code alone would not catch a feature revert. Semantics live in the CliRunner
    units named in the module docstring.

    Fail-under-revert per surface:
    - `close` / `link` / `unlink` / `transition --force` (HATS-371): command or
      flag removal → click exit 2 → step fails.
    - `task show` linked context (HATS-691): the ``"Linked context:"`` marker is
      produced only by the linked-context block — its presence (default) /
      absence (``--short``) is the revert signal.
    - `hyp create --verification-protocol` (HATS-623): flag removal → click
      rejects the unknown option → exit 2.
    - `hyp autoclose` (HATS-769): subcommand removal → click exit 2; the
      ``"closed:"`` marker proves the sweep ran. Quorum semantics (status flip,
      audit entry, negative case) live in test_cli_hyp.py + test_quorum.py.
    """
    launcher_dest, env, _venv = shared_launcher
    project = tmp_path / "project"
    project.mkdir()

    def ai_hats(*args, expect_exit=0, timeout=180):
        return _run(
            [str(launcher_dest), *args],
            cwd=project, env=env, timeout=timeout, expect_exit=expect_exit,
        )

    # ---- bootstrap project (venv is the session-shared build) ----
    ai_hats(
        "self", "init",
        "-r", "assistant", "-p", "claude",
        "--task-prefix", "TST",
    )

    # ---- create two tasks + list roundtrip ----
    ai_hats("task", "create", "First", "-d", "first task", "-p", "medium")
    ai_hats("task", "create", "Second", "-d", "second task", "-p", "medium")
    res = ai_hats("task", "list", "--all")
    assert "TST-001" in res.stdout, f"TST-001 missing:\n{res.stdout}"
    assert "TST-002" in res.stdout, f"TST-002 missing:\n{res.stdout}"

    # ---- close (HATS-371): fast-close brainstorm → done ----
    ai_hats("task", "close", "TST-001", "--resolution", "shipped on master")

    # ---- link related (HATS-371), then show its linked context (HATS-691) ----
    ai_hats("task", "link", "TST-002", "TST-001", "--type", "related")
    # Default `show` renders the linked-task bodies under a "Linked context:"
    # header — the HATS-691 revert signal (the link index alone has no header).
    res = ai_hats("task", "show", "TST-002")
    assert "Linked context:" in res.stdout, (
        f"linked context missing on default show:\n{res.stdout}"
    )
    # `--short` omits the bodies (flag wiring; revert → header reappears).
    res_short = ai_hats("task", "show", "TST-002", "--short")
    assert "Linked context:" not in res_short.stdout, (
        f"--short did not omit linked context:\n{res_short.stdout}"
    )

    # ---- unlink (HATS-371) ----
    ai_hats("task", "unlink", "TST-002", "TST-001")

    # ---- force transition (HATS-371): bypass the FSM guard ----
    ai_hats(
        "task", "transition", "TST-001", "brainstorm",
        "--force", "--reason", "e2e reopen test",
    )

    # ---- hyp create --verification-protocol (HATS-623) ----
    # The flag is the revert signal: removing it → click rejects the unknown
    # option (exit 2). The YAML roundtrip is owned by test_cli_hyp.py.
    res = ai_hats(
        "task", "hyp", "create",
        "--title", "lib change",
        "--hypothesis", "x causes y",
        "--source-task", "TST-001",
        "--verification-protocol", "Run suite X; observe metric Y unchanged",
    )
    assert "HYP-001" in res.stdout, f"hyp create did not report HYP-001:\n{res.stdout}"

    # ---- hyp autoclose (HATS-769) ----
    # Wiring only: the command resolves through the real binary and exits 0.
    # HYP-001 has no refuted verdicts → nothing closes → "closed: none" marker.
    # Revert signal: removing the subcommand → click exit 2.
    res = ai_hats("task", "hyp", "autoclose")
    assert "closed:" in res.stdout, f"hyp autoclose marker missing:\n{res.stdout}"
