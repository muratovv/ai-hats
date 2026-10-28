"""The holder for `ai-hats acp`: a WrapRunner whose surface speaks ACP on pipes."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
from typing import TYPE_CHECKING, Callable

from ai_hats_observe.artifacts import EVENT_LOG_JSONL

from ..wrap_runner import WrapRunner
from .argv import grok_acp_argv
from .relay import Relay

if TYPE_CHECKING:
    from ai_hats_observe import Session
    from ai_hats_observe.event_log_writer import EventLogWriter


class AcpRunner(WrapRunner):
    """One ACP client, one grok child, the journal on disk."""

    follows_main_record = False  # the ACP stream is the main agent's record
    refuses_unready = True  # no terminal to run a login in

    def __init__(self, *args, stdout_fd: int, stdin_fd: int = 0, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._stdout_fd = stdout_fd
        self._stdin_fd = stdin_fd
        self._child: subprocess.Popen[bytes] | None = None
        self._signal: int | None = None

    def _stdin_is_terminal(self) -> bool:
        return False  # stdin is the client's ACP, never a keyboard

    def _session_on_the_plan(
        self,
        session: Session,
        run,
        *,
        extra_args: list[str] | None,
        provider_session_id: str,
    ):
        """Skip the minted id. grok names the session in session/new, and a fresh
        UUID here would publish an empty directory in the person's grok home."""
        del provider_session_id
        return super()._session_on_the_plan(
            session, run, extra_args=extra_args, provider_session_id=""
        )

    def run(self, extra_args=None, tags=None, pty_tap_factory=None):
        del pty_tap_factory
        previous = {
            sig: signal.signal(sig, self._on_signal) for sig in (signal.SIGINT, signal.SIGTERM)
        }
        try:
            return super().run(extra_args=extra_args, tags=tags)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)

    def _on_signal(self, signum: int, _frame) -> None:
        if self._signal is None:
            self._signal = signum
        self._terminate_child()

    def _terminate_child(self, sig: int = signal.SIGTERM) -> None:
        child = self._child
        if child is not None and child.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(child.pid, sig)

    def _spawn_surface(
        self,
        cmd: list[str],
        env: dict[str, str],
        tracer,
        *,
        session: Session,
        event_log: EventLogWriter | None,
        provider_session_id: str,
        pty_tap_factory=None,
        on_spawn: Callable[[int], None],
        initial_prompt: str | None = None,
    ) -> int:
        del tracer, pty_tap_factory, provider_session_id, initial_prompt
        if self._signal is not None:
            return 128 + self._signal
        own_journal = event_log is None
        journal = event_log if event_log is not None else _open_journal(session)
        child = subprocess.Popen(
            grok_acp_argv(cmd),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            start_new_session=True,
        )
        self._child = child
        on_spawn(child.pid)
        log = session.session_dir / EVENT_LOG_JSONL
        relay = Relay(
            client_in=self._stdin_fd,
            client_out=self._stdout_fd,
            child=child,
            event_log=journal,
            log_path=str(log),
            on_session=session.record_provider_session_id,
            skill_names=_installed_skills(self.layout, session.session_id),
        )
        code = 1
        try:
            code = relay.run()
            _retain_grok_transcript(env, self.layout.cwd, session)
            return code
        finally:
            if child.poll() is None:
                self._terminate_child()
            if own_journal:
                # The shared starter writes no file for a surface with no reader.
                # This holder is the reader, so it closes the file itself.
                journal.close(exit_code=child.returncode if child.returncode is not None else code)


def _installed_skills(layout, session_id: str) -> set[str]:
    """Skill directory names this session mirrored. Grok lists them as commands."""
    root = layout.cache.session(session_id) / "skills"
    if not root.is_dir():
        return set()
    return {path.name for path in root.iterdir() if not path.name.startswith(".")}


def _retain_grok_transcript(env: dict[str, str], cwd, session: Session) -> None:
    """The id arrives from session/new, so the launch-time bind never published the file."""
    ids = session.provider_session_ids()
    home = env.get("GROK_HOME")
    if not ids or not home:
        return
    from pathlib import Path

    from ..surface_registry import get_surface

    get_surface("grok").retain_transcript(Path(home), Path(cwd), ids[-1])


def _open_journal(session: Session):
    """A writer that records only what this holder emits. Nothing is followed."""
    from ai_hats_observe.event_log_writer import EventLogWriter

    def locate():
        return []

    def reader_factory(path: object):
        raise RuntimeError(f"acp follows no surface record ({path})")

    return EventLogWriter(
        locate=locate,
        reader_factory=reader_factory,
        path=session.session_dir / EVENT_LOG_JSONL,
        report=session.log_sys,
    ).start()
