"""HATS-1151 — the one hook-execution primitive (ADR-0020 D2).

Contract tests run against the primitive directly so HATS-1141 inherits the
proof rather than repeating it.
"""

from __future__ import annotations

import os
import tracemalloc
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


def test_refusal_that_speaks_on_stderr_still_reaches_the_operator(tmp_path):
    """Printing the refusal to stderr is the ordinary shell habit, and stdout
    being empty must not swallow the verdict whole.

    Regression this pins: the reason was the stdout tail with no fallback, so
    this script produced ``reason == ''`` and the operator read
    ``failed on discard ()`` — strictly less than the pre-primitive runner,
    which at least named the script and the code. stdout still wins when it has
    something to say (ADR-0020 D2: a verbose stderr may never dilute a verdict);
    stderr is consulted only when stdout is silent.
    """
    script = _script(
        tmp_path / "quiet.sh",
        'echo "refusing: 3 unresolved review notes" >&2\nexit 2\n',
    )

    run = run_hook(script, timeout=10, project_dir=tmp_path)

    assert run.verdict is HookVerdict.REFUSE
    assert "refusing: 3 unresolved review notes" in run.reason
    assert str(script) in run.reason


def test_a_wholly_silent_refusal_still_names_itself(tmp_path):
    """Nothing on either stream: the reason must still identify the outcome and
    the script, never come back empty."""
    script = _script(tmp_path / "mute.sh", "exit 2\n")

    run = run_hook(script, timeout=10, project_dir=tmp_path)

    assert run.verdict is HookVerdict.REFUSE
    assert str(script) in run.reason
    assert "refused" in run.reason


def test_every_reason_names_the_script(tmp_path):
    """The skill is named by the caller, the script only ever by the reason —
    and ``hook broke: exited 1`` named neither it nor its log."""
    broke = run_hook(_script(tmp_path / "b.sh", "exit 1\n"), timeout=10, project_dir=tmp_path)
    slow = run_hook(_script(tmp_path / "s.sh", "sleep 5\n"), timeout=0.4, project_dir=tmp_path)

    assert str(tmp_path / "b.sh") in broke.reason
    assert str(tmp_path / "s.sh") in slow.reason


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
        'for i in $(seq 1 400); do echo "line $i padding padding padding"; done\n'
        'echo "FAILED tests/test_kernel.py::test_persist_once"\n'
        "exit 2\n",
    )

    run = run_hook(script, timeout=20, project_dir=tmp_path, log_path=log, tail_bytes=256)

    assert run.truncated
    assert "FAILED tests/test_kernel.py::test_persist_once" in run.reason
    assert str(log) in run.reason
    assert "line 1 padding" in log.read_text()  # the full stream survives on disk


def test_a_small_truncation_reports_bytes_not_zero_kibibytes(tmp_path):
    """Integer-dividing by 1024 rendered every sub-KiB truncation as
    ``(0 KiB total)`` — a note that says nothing was there while cutting text
    away. Reachable from any caller with a small ``tail_bytes``, including this
    file's own truncation test.
    """
    script = _script(tmp_path / "small.sh", "echo abcdefghijklmnopqrstuvwxyz\nexit 2\n")

    run = run_hook(script, timeout=10, project_dir=tmp_path, tail_bytes=8)

    assert run.truncated
    assert "0 KiB" not in run.reason
    assert "27 B total" in run.reason


def test_log_keeps_both_streams_while_the_reason_keeps_only_stdout(tmp_path):
    """The verdict must not be diluted by diagnostics, but the log is what an
    operator opens when a hook fails — ``uv pip install`` reports progress on
    stderr, so dropping it there would blind the provisioning postmortem.
    """
    script = _script(
        tmp_path / "both.sh",
        'echo "noisy diagnostic" >&2\necho "the verdict"\nexit 2\n',
    )
    log = tmp_path / "logs" / "both.log"

    run = run_hook(script, timeout=10, project_dir=tmp_path, log_path=log)

    assert run.reason == "the verdict"
    assert "noisy diagnostic" in run.stderr
    body = log.read_text()
    assert "the verdict" in body
    assert "noisy diagnostic" in body


def test_a_hook_that_removes_its_own_log_still_returns_an_outcome(tmp_path):
    """The primitive's contract is an outcome, never an exception.

    ``_tail`` stat'ed the sink unguarded, so a hook that cleaned up the log
    directory — and this one **passed** — raised ``FileNotFoundError`` straight
    through ``run_worktree_hook`` into ``before_teardown``, whose module
    docstring promises it never raises on hook failure. The ordering makes it
    unavoidable: the tail is read before stderr is folded back in, which would
    otherwise re-create the file.
    """
    logs = tmp_path / "logs"
    script = _script(tmp_path / "selfclean.sh", f'echo hi\nrm -rf "{logs}"\nexit 0\n')

    run = run_hook(script, timeout=10, project_dir=tmp_path, log_path=logs / "h.log")

    assert run.verdict is HookVerdict.PASS
    assert "unreadable" in run.reason


def test_an_unusable_log_path_is_a_governed_outcome(tmp_path):
    """A log path that cannot be opened fails closed and says so, rather than
    raising out of the primitive. HATS-1141 points this at
    ``tasks/<ID>/.checks/<event>.log``, where a name collision is far likelier
    than under the worktree state dir.
    """
    collision = tmp_path / "taken"
    collision.mkdir()

    run = run_hook(
        _script(tmp_path / "ok.sh", "exit 0\n"),
        timeout=10,
        project_dir=tmp_path,
        log_path=collision,
    )

    assert run.verdict is HookVerdict.CORRUPT
    assert not run.downgradable
    assert str(collision) in run.reason


def test_a_loud_stderr_never_lands_in_the_parents_memory(tmp_path):
    """HATS-823 D7, restated: child output streams to disk, never into the
    parent's memory.

    ``stderr=subprocess.PIPE`` accumulated the whole stream in RAM only for
    ``_decode_tail`` to throw all but 4 KiB of it away — measured at +716 MB of
    RSS for a 300 MB stderr, against 0 MB for the pre-primitive runner. The live
    shape is the venv-provisioning hook: ``uv pip install`` reports progress on
    stderr, which is exactly the stream that was unbounded.
    """
    mib = 16
    script = _script(
        tmp_path / "loud.sh",
        f'head -c {mib * 1024 * 1024} /dev/zero | tr "\\0" "x" >&2\necho done\nexit 0\n',
    )
    log = tmp_path / "loud.log"

    tracemalloc.start()
    try:
        run = run_hook(script, timeout=120, project_dir=tmp_path, log_path=log)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert run.verdict is HookVerdict.PASS
    assert peak < 4 * 1024 * 1024, f"{peak} bytes held for a {mib} MiB stderr"
    assert log.stat().st_size > mib * 1024 * 1024  # the whole stream survives on disk


def test_the_log_says_who_ran_and_the_reason_never_repeats_it(tmp_path):
    """The pre-primitive wt runner stamped provenance as the log's first line;
    the migration dropped it silently.

    It cannot simply be written back, because the reason is the tail of that
    same file — for a hook that prints nothing the tail would BE the header, and
    the operator would read our own bookkeeping as the hook's verdict. So the
    sink records where the child's output starts and the tail begins there.
    """
    log = tmp_path / "wt.log"
    header = "# wt-hook event=discard script=mute.sh timeout=45s"

    run = run_hook(
        _script(tmp_path / "mute.sh", "exit 2\n"),
        timeout=10,
        project_dir=tmp_path,
        log_path=log,
        log_header=header,
    )

    assert header in log.read_text()
    assert "wt-hook event" not in run.reason
    assert "refused" in run.reason


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
    the full timeout (ADR-0020 D2).

    fd 0 is deliberately made a live pipe with a writer still open, because the
    ambient one proves nothing: pytest's default ``--capture=fd`` already
    ``dup2``s ``/dev/null`` onto fd 0, so a child that *inherited* stdin would
    read EOF and pass anyway. Under this pipe, only ``stdin=DEVNULL`` passes —
    dropping it makes the hook block until the timeout.
    """
    read_fd, write_fd = os.pipe()
    saved = os.dup(0)
    try:
        os.dup2(read_fd, 0)
        run = run_hook(
            _script(tmp_path / "rd.sh", "read x || true\nexit 0\n"),
            timeout=5,
            project_dir=tmp_path,
        )
    finally:
        os.dup2(saved, 0)
        for fd in (saved, read_fd, write_fd):
            os.close(fd)

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
