"""HATS-1151 — the one hook-execution primitive (ADR-0020 D2).

Contract tests run against the primitive directly so HATS-1141 inherits the
proof rather than repeating it.
"""

from __future__ import annotations

from pathlib import Path

from ai_hats.hook_exec import HookVerdict, run_hook


def _script(p: Path, body: str) -> Path:
    p.write_text("#!/usr/bin/env bash\n" + body)
    p.chmod(0o755)
    return p


def test_refusal_carries_the_scripts_own_words(tmp_path):
    """exit 2 is a verdict, and the reason is what the script actually said.

    The defect this card exists for: the wt runner returned
    ``hook exited 2: <script>`` and dropped the script's explanation into a log
    file, so whoever got blocked learned nothing actionable.
    """
    script = _script(
        tmp_path / "refuse.sh",
        'echo "[hunk-review] 3 unresolved notes in .hunk/notes.json"\n'
        'echo "run: bash hunk-notes.sh consume"\n'
        "exit 2\n",
    )

    run = run_hook(script, timeout=10, project_dir=tmp_path)

    assert run.verdict is HookVerdict.REFUSE
    assert run.exit_code == 2
    assert "3 unresolved notes in .hunk/notes.json" in run.reason
    assert "run: bash hunk-notes.sh consume" in run.reason


def test_missing_script_is_corrupt_and_not_downgradable(tmp_path):
    """ADR-0019 D4: a bound script that is not there is infrastructure
    corruption, and ``on_error: warn`` must not be able to soften it —
    "otherwise every warn-binding becomes a way to disarm a gate by deleting a
    file". A flat pass/refuse/broke cannot express that, so it is its own class.
    """
    run = run_hook(tmp_path / "gone.sh", timeout=10, project_dir=tmp_path)

    assert run.verdict is HookVerdict.CORRUPT
    assert not run.downgradable
    assert "gone.sh" in run.reason


def test_exit_one_is_broke_and_downgradable(tmp_path):
    """Refuse is exit 2, deliberately not 1: under ``set -e`` a false ``[[ ]]``,
    a ``grep`` with no match and a failing ``jq`` all abort with 1, so 1 is the
    status a shell check produces *by accident*. It routes to the caller's
    error policy, never reads as a considered verdict (ADR-0020 D2).
    """
    run = run_hook(_script(tmp_path / "oops.sh", "exit 1\n"), timeout=10, project_dir=tmp_path)

    assert run.verdict is HookVerdict.BROKE
    assert run.exit_code == 1
    assert run.downgradable


def test_timeout_is_broke_not_refuse(tmp_path):
    """A hung check formed no verdict, so a timeout is broke (ADR-0020 D2)."""
    run = run_hook(_script(tmp_path / "slow.sh", "sleep 5\n"), timeout=0.4, project_dir=tmp_path)

    assert run.verdict is HookVerdict.BROKE
    assert "timed out" in run.reason


def test_truncated_reason_points_at_the_full_log(tmp_path):
    """A suite-running gate emits far more than the tail holds. The agent gets
    the last lines plus a path it can open — the reason must never imply it is
    the whole story when it is not.
    """
    log = tmp_path / "logs" / "edge.log"
    script = _script(
        tmp_path / "loud.sh",
        "for i in $(seq 1 400); do echo \"line $i padding padding padding\"; done\n"
        'echo "FAILED tests/test_kernel.py::test_persist_once"\n'
        "exit 2\n",
    )

    run = run_hook(script, timeout=20, project_dir=tmp_path, log_path=log, tail_bytes=256)

    assert run.truncated
    assert "FAILED tests/test_kernel.py::test_persist_once" in run.reason
    assert str(log) in run.reason
    assert "line 1 padding" in log.read_text()  # the full stream survives on disk


def test_pass(tmp_path):
    run = run_hook(_script(tmp_path / "ok.sh", "exit 0\n"), timeout=10, project_dir=tmp_path)
    assert run.verdict is HookVerdict.PASS
    assert run.ok


def test_non_executable_is_corrupt(tmp_path):
    p = tmp_path / "ne.sh"
    p.write_text("#!/usr/bin/env bash\nexit 0\n")  # no +x
    run = run_hook(p, timeout=10, project_dir=tmp_path)
    assert run.verdict is HookVerdict.CORRUPT
    assert not run.downgradable


def test_exit_127_is_corrupt_not_broke(tmp_path):
    """127 means the script never ran — no verdict to downgrade (ADR-0019 D4)."""
    run = run_hook(_script(tmp_path / "nf.sh", "exit 127\n"), timeout=10, project_dir=tmp_path)
    assert run.verdict is HookVerdict.CORRUPT
    assert not run.downgradable
    assert "127" in run.reason


def test_signal_death_is_broke(tmp_path):
    run = run_hook(
        _script(tmp_path / "sig.sh", "kill -TERM $$\n"), timeout=10, project_dir=tmp_path
    )
    assert run.verdict is HookVerdict.BROKE
    assert "signal" in run.reason


def test_invalid_utf8_is_governed_not_a_stack_trace(tmp_path):
    """``text=True`` is strict UTF-8 and raises inside the caller; the primitive
    decodes with ``errors='replace'`` so bad bytes become a governed reason."""
    run = run_hook(
        _script(tmp_path / "bin.sh", "printf 'caf\\xe9 broke'\nexit 2\n"),
        timeout=10,
        project_dir=tmp_path,
    )
    assert run.verdict is HookVerdict.REFUSE
    assert "broke" in run.reason


def test_stdin_is_closed_so_a_reading_hook_cannot_hang(tmp_path):
    """An interactive ``read`` must fail fast, never hold the caller's lock for
    the full timeout (ADR-0020 D2)."""
    run = run_hook(
        _script(tmp_path / "rd.sh", "read x || true\nexit 0\n"), timeout=5, project_dir=tmp_path
    )
    assert run.verdict is HookVerdict.PASS


def test_cwd_and_env_come_from_the_caller(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    script = _script(tmp_path / "e.sh", 'echo "$AI_HATS_EVENT|$(pwd -P)"\nexit 2\n')

    run = run_hook(
        script,
        timeout=10,
        project_dir=proj,
        env={"AI_HATS_EVENT": "edge:review--done", "PATH": "/usr/bin:/bin"},
    )

    event, cwd = run.reason.split("|")
    assert event == "edge:review--done"
    assert Path(cwd).resolve() == proj.resolve()
