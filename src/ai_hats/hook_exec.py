"""One hook-execution primitive — ADR-0020 D2 (HATS-1151).

The primitive owns mechanics; callers own policy. The reason is the tail of the
child's stdout, so a refusing hook states its case in the caller's own output
rather than leaving a status code and a log path; stderr is captured separately
so a verbose diagnostic stream cannot push the verdict out of the tail.
"""

from __future__ import annotations

import os
import re
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Mapping, Sequence

from ai_hats_core.deadline import Deadline

from .env import (
    AI_HATS_PROJECT_DIR_ENV,
    ENV_FORCE,
    ENV_HOOK_POINT,
    ENV_IN_HOOK,
    ENV_TASK_ID,
    ENV_TASKS_DIR,
    ENV_WORKTREE_PATH,
)

# Big enough for a multi-line instruction, not just a verdict line.
REASON_TAIL_BYTES = 4096

# CSI sequences, OSC strings (BEL- or ST-terminated) and the single-char Fe set.
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])")
_STDERR_TAIL_BYTES = 4096


class HookVerdict(Enum):
    """Outcome classes of one hook run (ADR-0020 D2 + ADR-0019 D4)."""

    PASS = "pass"  # noqa: S105 — an outcome name, not a credential
    REFUSE = "refuse"
    BROKE = "broke"
    CORRUPT = "corrupt"


class HookOutcomeKind(Enum):
    """What happened to the child, apart from how this module words it.

    ``HookVerdict`` says how a channel must TREAT a run; this says WHY, so a
    channel can phrase it in its own vocabulary. The checks channel needs that:
    "hook" names a real channel there, and calling a binding line one misleads
    (HATS-1572). Matching on ``reason`` would work today and rot on the first
    rewording, so the fact travels as a value.
    """  # comment-length: allow — why this sits beside HookVerdict is the contract

    PASSED = "passed"
    REFUSED = "refused"
    EXITED = "exited"
    TIMED_OUT = "timed_out"
    SIGNALLED = "signalled"
    SCRIPT_MISSING = "script_missing"
    NOT_EXECUTABLE = "not_executable"
    NO_TIME_LEFT = "no_time_left"
    COMMAND_NOT_FOUND = "command_not_found"
    EXEC_FAILED = "exec_failed"
    LOG_UNUSABLE = "log_unusable"


@dataclass(frozen=True)
class HookRun:
    """One hook's governed outcome."""

    verdict: HookVerdict
    exit_code: int | None
    reason: str
    #: The fact behind the verdict — see :class:`HookOutcomeKind`.
    kind: HookOutcomeKind = field(kw_only=True)
    #: The child's own stdout tail, apart from the named outcome ``reason`` joins
    #: it to, so a channel can reword the second and keep the first verbatim.
    said: str = ""
    #: The FACT behind the outcome — a path, an errno, a signal, a budget — with
    #: none of this module's vocabulary in it. A channel that rewords the outcome
    #: keeps the fact; without this it could only reword by discarding (HATS-1572).
    detail: str = ""
    stderr: str = ""
    log_path: Path | None = None
    truncated: bool = False
    #: Bytes the child wrote, for the truncation note a channel appends itself.
    output_size: int = 0

    @property
    def ok(self) -> bool:
        return self.verdict is HookVerdict.PASS

    @property
    def downgradable(self) -> bool:
        """Whether ``on_error: warn`` may soften this outcome (ADR-0019 D4).

        Only a check's own failure qualifies. Corruption — a script missing, not
        executable, or unable to exec — is never downgradable, else a
        warn-binding becomes a way to disarm a gate by deleting a file.
        """
        return self.verdict is HookVerdict.BROKE


def run_hook(
    script: Path,
    *,
    point: str,
    budget: float,
    deadline: Deadline,
    project_dir: Path,
    force: bool = False,
    task_id: str | None = None,
    worktree_path: Path | None = None,
    tasks_dir: Path | None = None,
    extra_env: Mapping[str, str] | None = None,
    log_path: Path | None = None,
    tail_bytes: int = REASON_TAIL_BYTES,
    argv: Sequence[str] = (),
    stdin_payload: bytes | None = None,
    tee: bool = False,
    drop_env: Sequence[str] = (),
    cwd: Path | None = None,
) -> HookRun:
    """Run ``script`` under the D2 contract and return its outcome.

    ``point`` is the fully-qualified attachment point (``review->done``,
    ``wt:teardown[discard]``); it names the run in the env and the log header,
    so a channel never hands in its own strings for those. ``script`` must
    already be absolute — resolution belongs to the caller. ``KeyboardInterrupt``
    propagates regardless of the caller's error policy.

    ``budget`` is what the channel asks for, ``deadline`` what the caller is
    bounded by; the run gets the smaller, so no channel compares its own
    constant against a lock (HATS-1593).

    The keyword arguments below widen the primitive to the git channel
    (HATS-1828). Each defaults to what the other four channels already do, so
    their path through this function is unchanged:

    * ``argv`` — arguments the channel's own protocol hands the script. git
      passes them; the declarative channels have none.
    * ``stdin_payload`` — a FINITE buffer written to the child, then EOF. D2's
      default stays ``DEVNULL``; what it forbids is an open pipe a hook can wait
      on forever, which a buffer closed immediately is not.
    * ``tee`` — also copy the child's streams to this process's own, for a
      channel whose output belongs to a human watching it live. The reason is
      still read from the sink, so the verdict is unaffected either way.
    * ``drop_env`` — names the channel removes from the inherited environment.
      ``extra_env`` can only add, and git must be able to strip the venv and
      identity keys travelling with a foreign session pin (ADR-0025 D3) before
      any gate, drop-in or chained hook sees them.
    * ``cwd`` — where the child runs; ``project_dir`` when unset. git is the one
      channel where the two differ: a commit inside a linked worktree must have
      its gates inspect THAT tree, and a gate rooting itself with
      ``git rev-parse --show-toplevel`` from the main checkout would validate the
      wrong one and pass — worse than failing (ADR-0019 D5/D7).
    """  # comment-length: allow — the D2 execution contract itself
    if not script.is_file():
        return _corrupt(
            f"hook script missing: {script}",
            None,
            HookOutcomeKind.SCRIPT_MISSING,
            detail=str(script),
        )
    if not os.access(script, os.X_OK):
        return _corrupt(
            f"hook script not executable: {script}",
            None,
            HookOutcomeKind.NOT_EXECUTABLE,
            detail=str(script),
        )

    timeout = deadline.budget_for(budget)
    if timeout <= 0.0:
        return HookRun(
            verdict=HookVerdict.BROKE,
            exit_code=None,
            reason=f"hook broke: no time left under {deadline.origin}: {script}",
            kind=HookOutcomeKind.NO_TIME_LEFT,
            detail=deadline.origin,
        )

    header = f"# hook point={point} script={script} timeout={timeout}s under {deadline.origin}"
    try:
        sink, sink_path, said_from = _open_stdout_sink(log_path, header)
    except OSError as exc:
        # Fail closed and name the path: the caller asked for a log there, and
        # running the gate while silently dropping its evidence is the absence
        # this channel exists to make loud.
        return _corrupt(
            f"hook log path unusable ({type(exc).__name__}): {exc}",
            None,
            HookOutcomeKind.LOG_UNUSABLE,
            detail=f"({type(exc).__name__}): {exc}",
        )
    err_sink, err_path = _open_scratch_sink()
    expired: subprocess.TimeoutExpired | None = None
    returncode: int | None = None
    cmd = [str(script), *argv]
    run_in = str(project_dir if cwd is None else cwd)
    env = _hook_env(point, project_dir, force, task_id, worktree_path, tasks_dir, extra_env)
    for name in drop_env:
        env.pop(name, None)
    try:
        try:
            if tee:
                returncode = _run_teed(
                    cmd,
                    cwd=run_in,
                    env=env,
                    payload=stdin_payload,
                    sinks=(sink, err_sink),
                    timeout=timeout,
                )
            else:
                completed = subprocess.run(  # noqa: S603 — spawning the caller's hook IS the contract; no shell
                    cmd,
                    cwd=run_in,
                    env=env,
                    # `input` and `stdin` are mutually exclusive in `run`, so the
                    # absent payload is what selects D2's default.
                    **(
                        {"stdin": subprocess.DEVNULL}
                        if stdin_payload is None
                        else {"input": stdin_payload}
                    ),
                    stdout=sink,
                    stderr=err_sink,
                    timeout=timeout,
                )
                returncode = completed.returncode
        except subprocess.TimeoutExpired as exc:
            expired = exc
        except OSError as exc:
            return _corrupt(
                f"hook could not be executed ({type(exc).__name__}): {exc}",
                log_path,
                HookOutcomeKind.EXEC_FAILED,
                detail=f"({type(exc).__name__}): {exc}",
            )
        finally:
            sink.close()
            err_sink.close()

        # Tail first, then fold stderr in: the reason must stay stdout-first,
        # while the log stays the whole picture an operator opens afterwards.
        said, truncated, size = _tail(sink_path, tail_bytes, start=said_from)
        stderr, _, _ = _tail(err_path, _STDERR_TAIL_BYTES)
        _append_stderr(log_path, err_path)

        if expired is not None:
            return HookRun(
                verdict=HookVerdict.BROKE,
                exit_code=None,
                reason=_note_truncation(
                    _join(f"hook broke: timed out after {timeout}s: {script}", said or stderr),
                    truncated,
                    size,
                    log_path,
                ),
                kind=HookOutcomeKind.TIMED_OUT,
                said=said,
                detail=f"after {timeout:g}s",
                stderr=stderr,
                log_path=log_path,
                truncated=truncated,
                output_size=size,
            )

        assert returncode is not None  # noqa: S101 — the three exits above are exhaustive
        verdict = _classify(returncode)
        named = _diagnosis(verdict, returncode, script)
        return HookRun(
            verdict=verdict,
            exit_code=returncode,
            reason=_note_truncation(
                _reason(verdict, named, said, stderr), truncated, size, log_path
            ),
            kind=_kind(returncode),
            said=said,
            detail=_detail(returncode),
            stderr=stderr,
            log_path=log_path,
            truncated=truncated,
            output_size=size,
        )
    finally:
        # In a finally so the OSError return and a propagating KeyboardInterrupt
        # leave nothing behind in $TMPDIR either.
        err_path.unlink(missing_ok=True)  # safe-delete: ok own scratch sink, already read
        if log_path is None:
            sink_path.unlink(missing_ok=True)  # safe-delete: ok own scratch sink, already read


#: One read/write per selector wake-up. Big enough that a chatty hook is not
#: pumped a syscall at a time, small enough to stay off this process's heap.
_PUMP_CHUNK = 65536


def _run_teed(
    cmd: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    payload: bytes | None,
    sinks: tuple,
    timeout: float,
) -> int:
    """Spawn ``cmd``, copying each stream to its sink AND this process's own.

    Raises :class:`subprocess.TimeoutExpired` exactly as ``subprocess.run`` does —
    including killing the child first — so both spawn paths converge on one
    handler upstairs. stdin rides the same selector instead of being written up
    front: a child that fills its stdout pipe before draining stdin would
    otherwise deadlock against a parent blocked on the write.

    The child's stdout is a pipe here rather than the terminal, so `isatty()` is
    false where a direct spawn made it true. That costs nothing this channel
    wanted: `_hook_env` already turns colour off for every hook.
    """  # comment-length: allow — the deadlock and the isatty change are the contract
    out_sink, err_sink = sinks
    proc = subprocess.Popen(  # noqa: S603 — spawning the caller's hook IS the contract; no shell
        cmd,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL if payload is None else subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    expires_at = time.monotonic() + timeout
    pending = memoryview(payload) if payload else None
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ, (out_sink, sys.stdout))
    sel.register(proc.stderr, selectors.EVENT_READ, (err_sink, sys.stderr))
    if proc.stdin is not None:
        if pending is None:
            proc.stdin.close()
        else:
            sel.register(proc.stdin, selectors.EVENT_WRITE, None)
    try:
        while sel.get_map():
            left = expires_at - time.monotonic()
            if left <= 0:
                raise subprocess.TimeoutExpired(cmd, timeout)
            for key, _mask in sel.select(timeout=left):
                if key.data is None:
                    pending = _pump_stdin(sel, key.fileobj, pending)
                else:
                    _pump_out(sel, key.fileobj, *key.data)
        return proc.wait(timeout=max(0.0, expires_at - time.monotonic()))
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise
    finally:
        sel.close()
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None and not stream.closed:
                stream.close()


def _pump_stdin(sel: selectors.BaseSelector, stream, pending: memoryview | None):
    """Write what fits, return what is left; unregister once the buffer is spent.

    Raw ``os.write`` rather than the buffered writer: this needs the count the
    kernel actually took, which is the whole point of pumping instead of blocking.
    """
    try:
        written = os.write(stream.fileno(), pending)
    except BrokenPipeError:
        # A hook that does not read its protocol is within its rights — git's own
        # `pre-push` sample ignores stdin. Nothing is owed once the child hung up.
        written = len(pending)
    rest = pending[written:]
    if not rest:
        sel.unregister(stream)
        stream.close()
        return None
    return rest


def _pump_out(sel: selectors.BaseSelector, stream, sink, mirror) -> None:
    """Copy one chunk to the sink (the verdict's source) and to ``mirror``."""
    chunk = stream.read1(_PUMP_CHUNK)
    if not chunk:
        sel.unregister(stream)
        stream.close()
        return
    sink.write(chunk)
    _mirror(chunk, mirror, sink)


def _mirror(chunk: bytes, mirror, sink) -> None:
    """Echo ``chunk`` to this process's stream; on failure say so IN the sink.

    The sink is the reported channel — it becomes the reason and the log — so a
    lost echo is stated where the operator will actually read it, rather than
    swallowed or escalated into a verdict the hook never earned.
    """
    try:
        buffer = getattr(mirror, "buffer", None)
        if buffer is None:
            mirror.write(chunk.decode("utf-8", errors="replace"))
        else:
            buffer.write(chunk)
        mirror.flush()
    except (OSError, ValueError) as exc:
        sink.write(f"\n(ai-hats: live output stopped: {exc})\n".encode())


def _hook_env(
    point: str,
    project_dir: Path,
    force: bool,
    task_id: str | None,
    worktree_path: Path | None,
    tasks_dir: Path | None,
    extra: Mapping[str, str] | None,
) -> dict[str, str]:
    """The shared base every hook receives (ADR-0020 D2), then the caller's own
    point-specific vocabulary on top.

    An unresolvable value is REMOVED from the inherited environment rather than
    left alone: the ambient one may carry another worktree's path, and a check
    that validates the wrong tree and passes is worse than one that crashes
    (ADR-0019 D5/D7).
    """
    env = dict(os.environ)
    # HATS-1161: an agent session carries FORCE_COLOR=3, and Rich honours it even
    # when stdout is no tty — every hook would then answer in escape sequences.
    for forcing in ("FORCE_COLOR", "CLICOLOR_FORCE", "CLICOLOR"):
        env.pop(forcing, None)
    env["NO_COLOR"] = "1"
    env[ENV_HOOK_POINT] = point
    env[AI_HATS_PROJECT_DIR_ENV] = str(project_dir)
    # A check must not re-enter the per-task lock from a subprocess (D5).
    env[ENV_IN_HOOK] = "1"
    _put(env, ENV_FORCE, "1" if force else None)
    _put(env, ENV_TASK_ID, task_id)
    _put(env, ENV_WORKTREE_PATH, str(worktree_path) if worktree_path else None)
    # HATS-1540: the primitive OWNS this one too, so a point that does not resolve
    # a backlog (`wt:pre-merge`) removes it rather than inheriting whatever the
    # ambient environment carries. Left to `extra`, which can only add, a stale
    # value reached the gate and a script comparing it to its own tracker read
    # "not my backlog" and waved the merge through — measured, not feared.
    _put(env, ENV_TASKS_DIR, str(tasks_dir) if tasks_dir else None)
    env.update(extra or {})
    return env


def _put(env: dict[str, str], name: str, value: str | None) -> None:
    if value is None:
        env.pop(name, None)
    else:
        env[name] = value


def _kind(code: int) -> HookOutcomeKind:
    """Exit status → the fact behind it — the sibling of :func:`_classify`, which
    maps the same status to how a channel must treat it."""
    if code == 0:
        return HookOutcomeKind.PASSED
    if code == 2:
        return HookOutcomeKind.REFUSED
    if code == 126:
        return HookOutcomeKind.NOT_EXECUTABLE
    if code == 127:
        return HookOutcomeKind.COMMAND_NOT_FOUND
    if code < 0 or code > 128:
        return HookOutcomeKind.SIGNALLED
    return HookOutcomeKind.EXITED


def _detail(code: int) -> str:
    """The fact a channel would otherwise have to re-derive from the status."""
    if code < 0:
        return f"signal {-code}"
    if code > 128:
        return f"signal {code - 128}"
    return ""


def _classify(code: int) -> HookVerdict:
    """Exit status → outcome class (ADR-0020 D2).

    126/127 are corruption rather than a check failure: the script never ran, so
    there is no verdict to downgrade (ADR-0019 D4).
    """
    if code == 0:
        return HookVerdict.PASS
    if code == 2:
        return HookVerdict.REFUSE
    if code in (126, 127):
        return HookVerdict.CORRUPT
    return HookVerdict.BROKE


def _reason(verdict: HookVerdict, named: str, said: str, stderr: str) -> str:
    """What the operator reads.

    A refusal that spoke on stdout stands alone — undiluted words are the whole
    point of the channel. Every other outcome, and a refusal that said nothing
    there, gets the named diagnosis (which carries the script) joined with
    whatever text exists. stderr is the fallback only when stdout is silent, so
    a verbose diagnostic stream still cannot dilute a real verdict (ADR-0020 D2)
    while a hook that reports the ordinary shell way is no longer swallowed.
    """
    if verdict is HookVerdict.PASS:
        return said
    if verdict is HookVerdict.REFUSE and said:
        return said
    return _join(named, said or stderr)


def _join(named: str, said: str) -> str:
    return f"{named}\n{said}" if said else named


def _diagnosis(verdict: HookVerdict, code: int, script: Path) -> str:
    """Name the outcome AND the script: the caller names the skill a binding
    came from, so the reason is the only place the script itself can appear."""
    return f"{_named_outcome(verdict, code)}: {script}"


def _named_outcome(verdict: HookVerdict, code: int) -> str:
    if verdict is HookVerdict.REFUSE:
        return f"hook refused (exit {code})"
    if code == 126:
        return "hook could not be executed: not executable (exit 126)"
    if code == 127:
        return "hook could not be executed: command not found (exit 127)"
    if code < 0:
        return f"hook broke: killed by signal {-code}"
    if code > 128:
        return f"hook broke: killed by signal {code - 128} (exit {code})"
    return f"hook broke: exited {code}"


def _append_stderr(log_path: Path | None, err_path: Path) -> None:
    """Fold the child's stderr into the log under a header, so the file keeps
    both streams the way the pre-HATS-1151 runner did (``stderr=STDOUT``).

    Copied stream-to-stream: the whole point of spooling stderr to disk is that
    no caller ever holds it, and reading it back to write it out would undo that.
    """
    if log_path is None:
        return
    try:
        if err_path.stat().st_size == 0:
            return
        with err_path.open("rb") as src, log_path.open("ab") as dst:
            dst.write(b"\n--- stderr ---\n")
            shutil.copyfileobj(src, dst)
    except OSError as exc:
        # The log is diagnostics; the verdict already stands. Losing the file
        # must not turn a governed outcome into a traceback.
        print(f"WARN: could not append hook stderr to {log_path}: {exc}", file=sys.stderr)


def _corrupt(
    reason: str, log_path: Path | None, kind: HookOutcomeKind, *, detail: str = ""
) -> HookRun:
    """Infrastructure corruption — never downgradable (ADR-0019 D4)."""
    return HookRun(
        verdict=HookVerdict.CORRUPT,
        exit_code=None,
        reason=reason,
        kind=kind,
        detail=detail,
        log_path=log_path,
    )


def _open_stdout_sink(log_path: Path | None, header: str | None = None):
    """A writable fd for the child's stdout, the path to read the tail from, and
    the offset the child's own output starts at.

    Streaming to a descriptor is what keeps the parent's memory bounded no matter
    how much the hook prints; the tail is read back afterwards. The offset is
    what lets the log carry a provenance header without it ever surfacing as the
    hook's words — for a silent hook the tail would otherwise be that header.
    """
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = log_path.open("wb")
        if header:
            fh.write(header.encode() + b"\n")
            fh.flush()
        return fh, log_path, fh.tell()
    sink, path = _open_scratch_sink()
    return sink, path, 0


def _open_scratch_sink():
    """A throwaway on-disk sink. stderr always gets one: a pipe would hold the
    whole stream in this process (HATS-823 D7 — ``uv pip install`` reports
    progress there), and only its tail is ever wanted."""
    tmp = tempfile.NamedTemporaryFile(prefix="ai-hats-hook-", suffix=".out", delete=False)
    return tmp, Path(tmp.name)


def _tail(path: Path, tail_bytes: int, *, start: int = 0) -> tuple[str, bool, int]:
    """Last ``tail_bytes`` written from ``start`` on, whether anything was cut,
    and how much the child itself wrote.

    A sink that vanished under us — a hook that cleans the directory its own log
    sits in — becomes a stated note, never an exception: this primitive's
    contract with every caller is an outcome.
    """
    try:
        size = max(0, path.stat().st_size - start)
        with path.open("rb") as fh:
            fh.seek(start + max(0, size - tail_bytes))
            raw = fh.read()
    except OSError as exc:
        return f"(output unreadable: {exc})", False, 0
    text, _ = _decode_tail(raw, tail_bytes)
    return text, size > tail_bytes, size


def with_truncation_note(reason: str, run: HookRun) -> str:
    """``reason``, plus where the rest of the output is when the tail was cut.

    Public because a channel that words the outcome itself still owes the
    operator this: a partial verdict read as a whole one is the silent
    truncation HATS-1137 already paid for once.
    """
    return _note_truncation(reason, run.truncated, run.output_size, run.log_path)


def _note_truncation(reason: str, truncated: bool, size: int, log_path: Path | None) -> str:
    """Say the reason is partial, and where the rest is — silence would read as
    the whole story. No pointer when there is no log to point at."""
    if not truncated:
        return reason
    note = f"— output truncated ({_human(size)} total)"
    if log_path is not None:
        note += f"; full output: {log_path}"
    return f"{reason}\n{note}" if reason else note


def _human(size: int) -> str:
    """Bytes below a kibibyte — ``size // 1024`` rendered them as ``0 KiB``."""
    return f"{size} B" if size < 1024 else f"{size // 1024} KiB"


def _decode_tail(raw: bytes, tail_bytes: int) -> tuple[str, bool]:
    """Decode the last ``tail_bytes`` of ``raw``; invalid bytes never raise.

    Escapes are stripped here rather than only neutralised in the env, because
    the env stops env-DRIVEN colour and nothing else: a hook that writes escapes
    deliberately would still corrupt the channel it is refusing through. The log
    file is untouched — it is the postmortem record, not the message.
    """
    cut = len(raw) > tail_bytes
    text = raw[-tail_bytes:].decode("utf-8", errors="replace")
    return _ANSI.sub("", text).strip(), cut
