"""One hook-execution primitive — ADR-0020 D2 (HATS-1151).

The primitive owns mechanics; callers own policy. The reason is the tail of the
child's stdout, so a refusing hook states its case in the caller's own output
rather than leaving a status code and a log path; stderr is captured separately
so a verbose diagnostic stream cannot push the verdict out of the tail.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping

# Big enough for a multi-line instruction, not just a verdict line.
REASON_TAIL_BYTES = 4096
_STDERR_TAIL_BYTES = 4096


class HookVerdict(Enum):
    """Outcome classes of one hook run (ADR-0020 D2 + ADR-0019 D4)."""

    PASS = "pass"  # noqa: S105 — an outcome name, not a credential
    REFUSE = "refuse"
    BROKE = "broke"
    CORRUPT = "corrupt"


@dataclass(frozen=True)
class HookRun:
    """One hook's governed outcome."""

    verdict: HookVerdict
    exit_code: int | None
    reason: str
    stderr: str = ""
    log_path: Path | None = None
    truncated: bool = False

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
    timeout: float,
    project_dir: Path,
    env: Mapping[str, str] | None = None,
    log_path: Path | None = None,
    log_header: str | None = None,
    tail_bytes: int = REASON_TAIL_BYTES,
) -> HookRun:
    """Run ``script`` under the D2 contract and return its outcome.

    ``script`` must already be absolute — resolution (and its containment
    question) belongs to the caller. ``KeyboardInterrupt`` propagates: SIGINT
    aborts the whole operation regardless of the caller's error policy.

    ``log_header`` is the caller's provenance line for the top of the log; it is
    excluded from the reason by offset, never read back as the hook's own words.
    """
    if not script.is_file():
        return _corrupt(f"hook script missing: {script}", None)
    if not os.access(script, os.X_OK):
        return _corrupt(f"hook script not executable: {script}", None)

    try:
        sink, sink_path, said_from = _open_stdout_sink(log_path, log_header)
    except OSError as exc:
        # Fail closed and name the path: the caller asked for a log there, and
        # running the gate while silently dropping its evidence is the absence
        # this channel exists to make loud.
        return _corrupt(f"hook log path unusable ({type(exc).__name__}): {exc}", None)
    err_sink, err_path = _open_scratch_sink()
    expired: subprocess.TimeoutExpired | None = None
    proc: subprocess.CompletedProcess[bytes] | None = None
    try:
        try:
            proc = subprocess.run(  # noqa: S603 — spawning the caller's hook IS the contract; no shell
                [str(script)],
                cwd=str(project_dir),
                env=dict(env) if env is not None else None,
                stdin=subprocess.DEVNULL,
                stdout=sink,
                stderr=err_sink,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            expired = exc
        except OSError as exc:
            return _corrupt(f"hook could not be executed ({type(exc).__name__}): {exc}", log_path)
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
                stderr=stderr,
                log_path=log_path,
                truncated=truncated,
            )

        assert proc is not None  # noqa: S101 — the three exits above are exhaustive
        verdict = _classify(proc.returncode)
        named = _diagnosis(verdict, proc.returncode, script)
        return HookRun(
            verdict=verdict,
            exit_code=proc.returncode,
            reason=_note_truncation(
                _reason(verdict, named, said, stderr), truncated, size, log_path
            ),
            stderr=stderr,
            log_path=log_path,
            truncated=truncated,
        )
    finally:
        # In a finally so the OSError return and a propagating KeyboardInterrupt
        # leave nothing behind in $TMPDIR either.
        err_path.unlink(missing_ok=True)  # safe-delete: ok own scratch sink, already read
        if log_path is None:
            sink_path.unlink(missing_ok=True)  # safe-delete: ok own scratch sink, already read


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


def _corrupt(reason: str, log_path: Path | None) -> HookRun:
    """Infrastructure corruption — never downgradable (ADR-0019 D4)."""
    return HookRun(verdict=HookVerdict.CORRUPT, exit_code=None, reason=reason, log_path=log_path)


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
        return f"(hook output unreadable: {exc})", False, 0
    text, _ = _decode_tail(raw, tail_bytes)
    return text, size > tail_bytes, size


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
    """Decode the last ``tail_bytes`` of ``raw``; invalid bytes never raise."""
    cut = len(raw) > tail_bytes
    return raw[-tail_bytes:].decode("utf-8", errors="replace").strip(), cut
