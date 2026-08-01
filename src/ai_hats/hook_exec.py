"""One hook-execution primitive — ADR-0020 D2 (HATS-1151).

The primitive owns mechanics; callers own policy. The reason is the tail of the
child's stdout, so a refusing hook states its case in the caller's own output
rather than leaving a status code and a log path; stderr is captured separately
so a verbose diagnostic stream cannot push the verdict out of the tail.
"""

from __future__ import annotations

import os
import subprocess
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
    tail_bytes: int = REASON_TAIL_BYTES,
) -> HookRun:
    """Run ``script`` under the D2 contract and return its outcome.

    ``script`` must already be absolute — resolution (and its containment
    question) belongs to the caller. ``KeyboardInterrupt`` propagates: SIGINT
    aborts the whole operation regardless of the caller's error policy.
    """
    if not script.is_file():
        return _corrupt(f"hook script missing: {script}", log_path)
    if not os.access(script, os.X_OK):
        return _corrupt(f"hook script not executable: {script}", log_path)

    sink, sink_path = _open_stdout_sink(log_path)
    expired: subprocess.TimeoutExpired | None = None
    proc: subprocess.CompletedProcess[bytes] | None = None
    try:
        proc = subprocess.run(  # noqa: S603 — spawning the caller's hook IS the contract; no shell
            [str(script)],
            cwd=str(project_dir),
            env=dict(env) if env is not None else None,
            stdin=subprocess.DEVNULL,
            stdout=sink,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        expired = exc
    except OSError as exc:
        return _corrupt(f"hook could not be executed ({type(exc).__name__}): {exc}", log_path)
    finally:
        sink.close()

    said, truncated, size = _tail(sink_path, tail_bytes)
    if expired is not None:
        stderr, _ = _decode_tail(expired.stderr or b"", _STDERR_TAIL_BYTES)
        return HookRun(
            verdict=HookVerdict.BROKE,
            exit_code=None,
            reason=_note_truncation(
                _join(f"hook broke: timed out after {timeout}s", said),
                truncated,
                size,
                log_path,
            ),
            stderr=stderr,
            log_path=log_path,
            truncated=truncated,
        )

    assert proc is not None  # noqa: S101 — the three exits above are exhaustive
    stderr, _ = _decode_tail(proc.stderr or b"", _STDERR_TAIL_BYTES)
    verdict = _classify(proc.returncode)
    return HookRun(
        verdict=verdict,
        exit_code=proc.returncode,
        reason=_note_truncation(
            _reason(verdict, proc.returncode, said), truncated, size, log_path
        ),
        stderr=stderr,
        log_path=log_path,
        truncated=truncated,
    )


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


def _reason(verdict: HookVerdict, code: int, said: str) -> str:
    """The child's own words for a verdict; a named diagnosis when it broke."""
    if verdict in (HookVerdict.PASS, HookVerdict.REFUSE):
        return said
    return _join(_diagnosis(code), said)


def _join(named: str, said: str) -> str:
    return f"{named}\n{said}" if said else named


def _diagnosis(code: int) -> str:
    if code == 126:
        return "hook could not be executed: not executable (exit 126)"
    if code == 127:
        return "hook could not be executed: command not found (exit 127)"
    if code < 0:
        return f"hook broke: killed by signal {-code}"
    if code > 128:
        return f"hook broke: killed by signal {code - 128} (exit {code})"
    return f"hook broke: exited {code}"


def _corrupt(reason: str, log_path: Path | None) -> HookRun:
    """Infrastructure corruption — never downgradable (ADR-0019 D4)."""
    return HookRun(
        verdict=HookVerdict.CORRUPT, exit_code=None, reason=reason, log_path=log_path
    )


def _open_stdout_sink(log_path: Path | None):
    """A writable fd for the child's stdout, plus the path to read the tail from.

    Streaming to a descriptor is what keeps the parent's memory bounded no matter
    how much the hook prints; the tail is read back afterwards.
    """
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        return log_path.open("wb"), log_path
    tmp = tempfile.NamedTemporaryFile(prefix="ai-hats-hook-", suffix=".out", delete=False)
    return tmp, Path(tmp.name)


def _tail(path: Path, tail_bytes: int) -> tuple[str, bool, int]:
    """Last ``tail_bytes`` of ``path`` as text, whether anything was cut, total size."""
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > tail_bytes:
            fh.seek(size - tail_bytes)
        raw = fh.read()
    text, _ = _decode_tail(raw, tail_bytes)
    return text, size > tail_bytes, size


def _note_truncation(reason: str, truncated: bool, size: int, log_path: Path | None) -> str:
    """Say the reason is partial, and where the rest is — silence would read as
    the whole story. No pointer when there is no log to point at."""
    if not truncated:
        return reason
    note = f"— output truncated ({size // 1024} KiB total)"
    if log_path is not None:
        note += f"; full output: {log_path}"
    return f"{reason}\n{note}" if reason else note


def _decode_tail(raw: bytes, tail_bytes: int) -> tuple[str, bool]:
    """Decode the last ``tail_bytes`` of ``raw``; invalid bytes never raise."""
    cut = len(raw) > tail_bytes
    return raw[-tail_bytes:].decode("utf-8", errors="replace").strip(), cut
