"""A real ai-hats session on a surface that holds instead of calling a model.

For e2e tests whose subject is what a RUN does — the recovery sweeps at
``create_session``, in HATS-1339's case — rather than what an agent says. The
alternative, :mod:`_helpers.hitl`, needs an authenticated ``claude`` and a TUI
that exits on cue; here the surface is a script whose only behaviour is to wait,
so a session can be held open, SIGKILLed, or made to exit at once.

Everything around that script is production code: the provider subclasses
:class:`~ai_hats.surfaces.claude.provider.ClaudeSurface`, so the session cache
(``prompt.md``, the ``plugin/skills`` mirror, ``settings.json``) and the run
artifacts under ``sessions/runs/`` are materialized by the real builders.

    surface = install(tmp_project, tmp_path, repo_root)
    held = surface.start_held("live")      # a session that stays up
    done = surface.run_once()              # a session that starts and exits

The surface is registered through a synthetic dist-info on ``PYTHONPATH`` (the
provider entry-point contract, HATS-870) — no install, no repo change.
"""  # comment-length: allow — the module IS the contract for its two consumers

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from ai_hats.session_liveness import ANCHOR_NAME

from _helpers.env import checkout_pythonpath, clean_env

#: Surface name the fake surface registers under.
SURFACE = "holdfast"

_PROVIDER_SRC = f'''\
import os
import sys

from ai_hats.surfaces.claude.provider import ClaudeSurface


class HoldfastProvider(ClaudeSurface):
    """The claude surface with its CLI swapped for a script that just waits."""

    @property
    def name(self):
        return "{SURFACE}"

    def get_cli_command(self, args=None):
        return [sys.executable, os.environ["FAKE_SURFACE_HOLD_SCRIPT"], *(args or [])]

    def engine(self):
        # None routes ``ai-hats agent`` down SubAgentRunner's legacy subprocess
        # path — pipes, no pty — which is where the surface child can outlive a
        # SIGKILLed wrapper (HATS-1339 D3). claude's SDK engine would hide it.
        return None

    def describe_automate_launch(self, *args, **kwargs):
        # ClaudeSurface describes SDK options, not an argv. Take the base
        # class's CLI description, which is what that legacy path executes.
        from ai_hats.surfaces import Surface

        return Surface.describe_automate_launch(self, *args, **kwargs)
'''

#: Waits out its hold and nothing else — no ``getppid() == 1`` self-exit, which
#: made every orphan in this tier polite and hid the one the sweep would strand.
#: What ends it when its parent is SIGKILLed is the kernel hanging up the pty
#: ``_pty_spawn`` gave it — the property the orphan test pins (HATS-1339 D3).
# comment-length: allow — the removed self-exit is the defect this tier missed
_HOLD_SRC = """\
import os
import sys
import time

pid_file = os.environ.get("FAKE_SURFACE_PID_FILE")
if pid_file:
    with open(pid_file, "w") as fh:
        fh.write(str(os.getpid()))
dump = os.environ.get("FAKE_SURFACE_ENV_DUMP")
if dump:
    import json

    with open(dump, "w") as fh:
        json.dump(dict(os.environ), fh)
sys.stdout.write("fake-surface up\\r\\n")
sys.stdout.flush()
deadline = time.monotonic() + float(os.environ.get("FAKE_SURFACE_HOLD_SECONDS", "0"))
while time.monotonic() < deadline:
    time.sleep(0.1)
"""

#: ai-hats installs no logging handler anywhere in the tree, so a sweep's INFO
#: record falls to ``logging.lastResort`` (WARNING) and vanishes. Wiring one is
#: the host's job, and the test IS the host — else no reclaim is observable.
_SITECUSTOMIZE = """\
import logging
import sys

logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(name)s %(message)s")
"""

#: Filename of :data:`_HOLD_SRC` on disk — also how a leaked surface is
#: recognised in ``ps`` before anything signals it.
HOLD_SCRIPT_NAME = "hold.py"

#: Long enough that a held session outlives the whole test, short enough that a
#: leaked one dies on its own.
HOLD_SECONDS = "300"
STARTUP_TIMEOUT_S = 60.0
RUN_TIMEOUT_S = 120.0


@dataclass(frozen=True)
class HeldSession:
    """One real ``ai-hats`` process parked on the fake surface."""

    proc: subprocess.Popen
    cache_dir: Path
    log: Path
    surface_pid_file: Path

    @property
    def sid(self) -> str:
        return self.cache_dir.name

    @property
    def pid(self) -> int:
        return self.proc.pid

    @property
    def surface_pid(self) -> int:
        """The pid of the surface CLI this session launched — the cache's reader.

        ``pid`` above owns the cache dir on paper; this is the process that
        actually reads ``plugin/skills`` and ``settings.json`` out of it.
        """
        return int(self.surface_pid_file.read_text())

    def tail(self) -> str:
        return self.log.read_text(errors="replace")[-2000:]

    def kill_and_reap(self) -> None:
        """SIGKILL the ai-hats process ALONE and reap it.

        One pid, never ``killpg``: the surface child is a session leader of its
        own (``_pty_spawn`` → ``setsid``), so a group kill would never have
        reached it anyway, and spelling it as one hid that from the reader.
        Reaping is load-bearing, not tidiness: an unreaped child stays a zombie,
        a zombie answers ``os.kill(pid, 0)`` and still holds a ``ps`` row, so its
        owner reads as alive and the staged crash would not be one.
        """  # comment-length: allow — one paragraph per non-obvious choice
        os.kill(self.pid, signal.SIGKILL)
        self.proc.wait(timeout=30)

    def kill_if_running(self) -> None:
        if self.proc.poll() is None:
            self.kill_and_reap()
        self._kill_leaked_surface()

    def _kill_leaked_surface(self) -> None:
        """Last-resort teardown for a surface child that outlived its wrapper.

        Only fires when the guarantee the suite pins has already broken, so a
        regression is a red test and not a machine full of held sessions. The
        argv is re-read from ``ps`` first: by teardown the recorded pid is
        normally dead, and signalling its REUSE would kill a stranger.
        """
        try:
            pid = self.surface_pid
        except (OSError, ValueError):
            return  # the surface never recorded a pid
        argv = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        if HOLD_SCRIPT_NAME not in argv:
            return  # exited, or the pid now belongs to someone else
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            return  # raced its own exit


@dataclass(frozen=True)
class FakeSurface:
    """A project wired to launch :data:`SURFACE`, plus the runs that drive it."""

    project: Path
    binary: Path
    env: dict[str, str]
    cache_home: Path
    tmp: Path

    def _argv(self, role: str) -> list[str]:
        return [str(self.binary), "-r", role, "-p", SURFACE]

    def _agent_argv(self, role: str) -> list[str]:
        # Default isolation (``discard``): the CLI exposes no "none", and the
        # worktree is beside the point here — the session cache lives in the
        # cache home either way.
        return [str(self.binary), "agent", role, "-p", SURFACE, "--task", "hold"]

    def start_held(self, name: str, *, role: str = "assistant", automate: bool = False):
        """Launch a session and wait until its cache dir is fully materialized.

        ``automate=True`` drives ``ai-hats agent`` instead of the bare HITL
        command — the same session cache, but the surface is spawned over pipes
        with no controlling tty, which is the only path where it can outlive its
        wrapper (HATS-1339 D3).
        """
        log = self.tmp / f"fake-surface-{name}.log"
        pid_file = self.tmp / f"fake-surface-{name}.pid"
        argv = self._agent_argv(role) if automate else self._argv(role)
        with log.open("wb") as sink:
            proc = subprocess.Popen(
                argv,
                cwd=str(self.project),
                env={
                    **self.env,
                    "FAKE_SURFACE_HOLD_SECONDS": HOLD_SECONDS,
                    "FAKE_SURFACE_PID_FILE": str(pid_file),
                },
                stdin=subprocess.DEVNULL,
                stdout=sink,
                stderr=subprocess.STDOUT,
                # Its own session, so the test's own shell never shares its group.
                start_new_session=True,
            )
        cache_dir = self._await_cache_dir(proc, log)
        self._await_surface_pid(proc, pid_file, log)
        return HeldSession(proc=proc, cache_dir=cache_dir, log=log, surface_pid_file=pid_file)

    def _await_surface_pid(self, proc: subprocess.Popen, pid_file: Path, log: Path) -> None:
        """Block until the surface CLI itself is up, not just its cache dir.

        The anchor lands before the launch step, so a caller that stopped at
        ``_await_cache_dir`` could kill the wrapper while the surface was still
        being spawned — and then read "no orphan" from a race.
        """
        deadline = time.monotonic() + STARTUP_TIMEOUT_S
        while time.monotonic() < deadline:
            if pid_file.is_file() and pid_file.read_text().strip():
                return
            if proc.poll() is not None:
                raise AssertionError(
                    f"the session exited (code {proc.returncode}) before its surface "
                    f"started; log tail:\n{log.read_text(errors='replace')[-2000:]}"
                )
            time.sleep(0.2)
        raise AssertionError(
            f"the surface never recorded a pid at {pid_file} within "
            f"{STARTUP_TIMEOUT_S:.0f}s; log tail:\n{log.read_text(errors='replace')[-2000:]}"
        )

    def _await_cache_dir(self, proc: subprocess.Popen, log: Path) -> Path:
        """The dir once its anchor exists — written after the artifacts, so no
        caller can race a half-built cache."""
        deadline = time.monotonic() + STARTUP_TIMEOUT_S
        suffix = f"-{proc.pid}"
        while time.monotonic() < deadline:
            for sessions in sorted(p for p in self.cache_home.glob("*/sessions") if p.is_dir()):
                for entry in sessions.iterdir():
                    if entry.name.endswith(suffix) and (entry / ANCHOR_NAME).is_file():
                        return entry
            if proc.poll() is not None:
                raise AssertionError(
                    f"the session exited (code {proc.returncode}) before it claimed a "
                    f"cache dir; log tail:\n{log.read_text(errors='replace')[-2000:]}"
                )
            time.sleep(0.2)
        raise AssertionError(
            f"no cache dir for pid {proc.pid} under {self.cache_home} within "
            f"{STARTUP_TIMEOUT_S:.0f}s; log tail:\n{log.read_text(errors='replace')[-2000:]}"
        )

    def run_once(self, *, role: str = "assistant") -> subprocess.CompletedProcess:
        """One session that starts and exits — the recovery sweeps fire at its start."""
        done = subprocess.run(
            self._argv(role),
            cwd=str(self.project),
            env={**self.env, "FAKE_SURFACE_HOLD_SECONDS": "0"},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=RUN_TIMEOUT_S,
        )
        if done.returncode != 0:
            raise AssertionError(
                f"the ai-hats run failed (exit {done.returncode}):\n"
                f"{done.stdout[-1500:]}\n{done.stderr[-1500:]}"
            )
        return done

    def run_agent_once(
        self, *, role: str = "assistant", extra_env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess:
        """One AUTOMATE session that starts and exits, with ``extra_env`` on the launch.

        The sibling of :meth:`run_once` for the sub-agent road: ``engine()`` is
        ``None``, so this goes down SubAgentRunner's subprocess path — the branch
        that hands the surface child a whole environment.
        """
        done = subprocess.run(
            self._agent_argv(role),
            cwd=str(self.project),
            env={**self.env, "FAKE_SURFACE_HOLD_SECONDS": "0", **(extra_env or {})},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=RUN_TIMEOUT_S,
        )
        if done.returncode != 0:
            raise AssertionError(
                f"the sub-agent run failed (exit {done.returncode}):\n"
                f"{done.stdout[-1500:]}\n{done.stderr[-1500:]}"
            )
        return done


def install(tmp_project, tmp_path: Path, repo_root: Path) -> FakeSurface:
    """Register the fake surface for ``tmp_project`` and return its driver."""
    plugin = tmp_path / "surface-plugin"
    plugin.mkdir()
    (plugin / "holdfast_provider.py").write_text(_PROVIDER_SRC)
    dist_info = plugin / "holdfast_hats-0.1.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: holdfast-hats\nVersion: 0.1\n"
    )
    (dist_info / "entry_points.txt").write_text(
        f"[ai_hats.providers]\n{SURFACE} = holdfast_provider:HoldfastProvider\n"
    )

    log_sink = tmp_path / "log-sink"
    log_sink.mkdir()
    (log_sink / "sitecustomize.py").write_text(_SITECUSTOMIZE)

    hold_script = tmp_path / HOLD_SCRIPT_NAME
    hold_script.write_text(_HOLD_SRC)

    env = clean_env(os.environ)
    env.update(tmp_project.env)
    env["AI_HATS_USER_HOME"] = str(tmp_path / "_ai_hats_user_home")
    # HATS-863: the checkout first, so a shared editable install pointing at
    # another checkout cannot serve the code under test.
    env["PYTHONPATH"] = os.pathsep.join(
        [checkout_pythonpath(repo_root), str(plugin), str(log_sink)]
    )
    env["FAKE_SURFACE_HOLD_SCRIPT"] = str(hold_script)
    env["HATS_SKIP_RETRO"] = "1"

    return FakeSurface(
        project=tmp_project.path,
        binary=tmp_project.ai_hats_binary,
        env=env,
        cache_home=Path(env["AI_HATS_CACHE_HOME"]),
        tmp=tmp_path,
    )
