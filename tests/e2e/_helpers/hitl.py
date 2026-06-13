"""HITL driver — drive bare ``ai-hats`` (no subcommand) via subprocess.

Counterpart to :mod:`_helpers.live` (sub-agent / SDK driver) and
:mod:`_helpers.project` (one-shot CLI driver). This module handles the
specific quirks of the bare-``ai-hats`` HITL path:

* Banner verification — ``runtime._print_session_start`` /
  ``runtime._print_session_end`` are plain ``print()`` calls in the
  parent ai-hats process (before / after ``_pty_spawn``), so their
  output lands in the captured stdout. They use ANSI SGR + CSI escapes
  so plain substring matching needs prior stripping.
* PTY child exit — ``stdin=DEVNULL`` hangs claude TUI (it ignores EOF
  on its child-PTY stdin). Empirically, ``stdin="/exit\\n\\x03\\x03"``
  (slash-command + double Ctrl-C) makes claude exit in ~1s with code
  130 (SIGINT). Codified as ``DEFAULT_EXIT_PAYLOAD``.
* Env hygiene — the test must NOT forward the developer shell wholesale
  into the subprocess: ``ANTHROPIC_API_KEY``, ``AWS_*``, ``GH_TOKEN``
  and friends would land in ``cp.stdout`` / ``cp.stderr`` and from
  there into CI failure logs. We pass a minimal allowlist plus the
  project's own env (``AI_HATS_*`` keys set by ``tmp_venv_project``).

Public surface::

    result = drive_bare_hitl(tmp_venv_project, role="assistant")
    (result
        .expect_start_banner(role="assistant", provider="claude")
        .expect_end_banner()
    )
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — typing only
    from .project import Project


# ---------------------------------------------------------------------------
# ANSI stripping — banners use bold + colour SGR; OSC for hyperlinks.
# ---------------------------------------------------------------------------

_ANSI_RE = re.compile(r"\x1b\[[0-9;?>=]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def strip_ansi(text: str) -> str:
    """Remove CSI + OSC escape sequences. Bare ``\\r`` is preserved
    (callers can ``.replace("\\r", "")`` if they need a fully clean line)."""
    return _ANSI_RE.sub("", text)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

#: Stdin payload that makes claude TUI exit promptly. ``/exit`` is the
#: documented slash command; the two ``\x03`` bytes are sent as raw
#: Ctrl-C keystrokes via the PTY proxy to force teardown if ``/exit``
#: alone isn't enough (older claude versions, or when the TUI is mid-
#: render). Exit code is then ``130`` (SIGINT) — tests should not assert
#: on a clean ``0``.
DEFAULT_EXIT_PAYLOAD = "/exit\n\x03\x03"

#: Minimal env keys forwarded to the child. ``HOME`` is required so
#: claude finds its config + credentials cache; ``PATH`` for the
#: launcher's pip / python lookup; ``TERM`` so the PTY layer can pick
#: a sane default. ``LANG`` / ``LC_*`` keep unicode glyphs (✨ etc.)
#: rendering correctly so banner assertions hit.
DEFAULT_ENV_ALLOWLIST: tuple[str, ...] = (
    "PATH", "HOME", "TERM", "LANG", "LC_ALL", "LC_CTYPE",
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HitlResult:
    """Outcome of one bare-``ai-hats`` HITL invocation.

    Fluent ``.expect_*()`` verbs (one verb = one assertion, each
    returns ``self``) mirror the idiom of
    :class:`tests.e2e._helpers.project.RunResult`.

    Use :attr:`stdout_plain` for assertions; raw :attr:`stdout` is kept
    for diagnostic dumps in failure messages.
    """

    cmd: tuple[str, ...]
    exit_code: int
    stdout: str             # raw, ANSI included
    stderr: str
    stdout_plain: str       # ANSI-stripped, what assertions match against
    duration_s: float
    timed_out: bool         # True if subprocess hit the timeout

    # ----- core verbs -----

    def expect_no_hang(self) -> "HitlResult":
        """Subprocess must NOT have hit the timeout."""
        if self.timed_out:
            raise AssertionError(
                "bare ai-hats hung past the timeout — claude TUI did not "
                "exit on stdin payload.\n"
                f"stdout_plain (tail 500):\n{self.stdout_plain[-500:]}"
            )
        return self

    def expect_start_banner(
        self, *, role: str, provider: str,
    ) -> "HitlResult":
        """The HITL session-start banner must be present in stdout.

        Format (from ``runtime._print_session_start``):
        ``[*] Role: <role> | Provider: <provider> | Session: <sid>``.
        """
        marker_role = f"[*] Role: {role}"
        marker_provider = f"Provider: {provider}"
        for marker in (marker_role, marker_provider):
            if marker not in self.stdout_plain:
                raise AssertionError(
                    f"start banner missing {marker!r}\n"
                    f"exit: {self.exit_code}\n"
                    f"stdout_plain (tail 800):\n{self.stdout_plain[-800:]}\n"
                    f"stderr (tail 400):\n{self.stderr[-400:]}"
                )
        return self

    def expect_end_banner(self) -> "HitlResult":
        """The HITL session-end banner must be present in stdout.

        Format (from ``runtime._print_session_end``):
        ``✨ Session <sid> complete!`` (followed by audit / trace /
        tokens / dir lines). The ``finally`` block in ``WrapRunner.run``
        runs even if claude crashed, so this should fire whenever the
        ai-hats process actually entered the launch step.
        """
        if "✨ Session" not in self.stdout_plain \
                or "complete!" not in self.stdout_plain:
            raise AssertionError(
                "end banner '✨ Session ... complete!' not observed.\n"
                f"exit: {self.exit_code}\n"
                f"stdout_plain (tail 800):\n{self.stdout_plain[-800:]}"
            )
        return self

    def expect_exit_in(self, codes: frozenset[int] | set[int]) -> "HitlResult":
        """``exit_code`` must be one of ``codes``.

        For the default exit payload (``/exit\\n\\x03\\x03``), claude
        teardown via SIGINT lands as ``130``. A clean ``/exit`` slash-
        command teardown (no Ctrl-C needed) lands as ``0``. Other
        codes are surprising and should fail the assertion. Tighter
        than ``expect_no_hang`` (which only excludes timeout) — pins
        the subprocess to a known-good outcome set.
        """
        if self.exit_code not in codes:
            raise AssertionError(
                f"unexpected exit code {self.exit_code}; expected one of "
                f"{sorted(codes)}.\n"
                f"stdout_plain (tail 500):\n{self.stdout_plain[-500:]}\n"
                f"stderr (tail 300):\n{self.stderr[-300:]}"
            )
        return self


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def _build_env(
    project_env: dict[str, str],
    allowlist: tuple[str, ...],
) -> dict[str, str]:
    """Compose subprocess env from minimal allowlist + project keys.

    Secrets in the parent shell (``ANTHROPIC_API_KEY``, ``AWS_*``,
    ``GH_TOKEN`` …) MUST NOT leak into the subprocess — they would
    surface in ``cp.stdout`` / ``cp.stderr`` and from there into CI
    failure logs.
    """
    env: dict[str, str] = {}
    for key in allowlist:
        val = os.environ.get(key)
        if val is not None:
            env[key] = val
    env.update(project_env)
    return env


def drive_bare_hitl(
    project: "Project",
    *,
    subcommand_args: tuple[str, ...] = (),
    role: str | None = None,
    extra_args: tuple[str, ...] = (),
    stdin_payload: str = DEFAULT_EXIT_PAYLOAD,
    timeout: float = 20.0,
    env_allowlist: tuple[str, ...] = DEFAULT_ENV_ALLOWLIST,
) -> HitlResult:
    """Drive an HITL ai-hats invocation (bare or subcommand) via subprocess.

    The bare invocation goes through the ``human`` YAML pipeline; HITL
    subcommands (``reflect all``, ``reflect role <name>``, etc.) take a
    Python pre-flight then ``WrapRunner._pty_spawn`` the provider CLI.
    Same PTY teardown contract — we feed ``stdin_payload`` (default
    :data:`DEFAULT_EXIT_PAYLOAD`) so the provider exits promptly.

    Returns a :class:`HitlResult` with the captured stdout + an
    ANSI-stripped view; ``expect_no_hang`` raises if the subprocess
    timed out.

    Parameters
    ----------
    project
        A :class:`tests.e2e._helpers.project.Project` providing
        ``ai_hats_binary`` (launcher) and ``env`` (project-specific
        keys like ``AI_HATS_VENV``).
    subcommand_args
        Click subcommand path inserted BEFORE ``-r <role>`` (HATS-546).
        Default ``()`` = bare ``ai-hats`` invocation. Examples:
        ``("reflect", "all")``, ``("reflect", "role", "maintainer")``.
        Position matters: Click resolves group → subcommand argv
        left-to-right, so the subcommand path must precede any options.
    role
        Optional ``-r <role>`` override on the bare path. ``None`` uses
        the project's ``default_role`` from ``ai-hats.yaml``. Some HITL
        subcommands (e.g. ``reflect all``) ignore ``-r`` — pass
        ``None`` in that case.
    extra_args
        Extra positional / option args appended after the role flag.
        For bare ai-hats: forwarded to the provider CLI. For HITL
        subcommands: subcommand-specific flags.
    stdin_payload
        Bytes (as str) written to subprocess stdin. The PTY proxy
        forwards this to claude's child-PTY stdin. See module docstring
        for empirical findings.
    timeout
        Hard cap on the subprocess. ``20s`` is enough for claude TUI
        to spawn + exit on the default payload; HITL subcommands with
        Python pre-flight (handoff build / composition materialize)
        may want ``30s+``.
    env_allowlist
        Keys forwarded from ``os.environ`` to the subprocess. Anything
        outside this list (plus ``project.env``) is dropped.
    """
    cmd: tuple[str, ...] = (str(project.ai_hats_binary),)
    cmd += tuple(subcommand_args)
    if role is not None:
        cmd += ("-r", role)
    cmd += tuple(extra_args)

    env = _build_env(project.env, env_allowlist)

    t0 = time.monotonic()
    timed_out = False
    try:
        cp = subprocess.run(
            cmd,
            cwd=str(project.path),
            env=env,
            input=stdin_payload,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        exit_code = cp.returncode
        stdout = cp.stdout
        stderr = cp.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = -1
        raw_stdout = exc.stdout
        raw_stderr = exc.stderr
        stdout = raw_stdout.decode(errors="replace") \
            if isinstance(raw_stdout, bytes) else (raw_stdout or "")
        stderr = raw_stderr.decode(errors="replace") \
            if isinstance(raw_stderr, bytes) else (raw_stderr or "")

    duration = time.monotonic() - t0
    return HitlResult(
        cmd=cmd,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        stdout_plain=strip_ansi(stdout),
        duration_s=duration,
        timed_out=timed_out,
    )
