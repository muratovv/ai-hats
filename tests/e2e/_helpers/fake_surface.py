"""A real ai-hats session on a surface that holds instead of calling a model.

For e2e tests whose subject is what a RUN does — the recovery sweeps at
``create_session``, in HATS-1339's case — rather than what an agent says. The
alternative, :mod:`_helpers.hitl`, needs an authenticated ``claude`` and a TUI
that exits on cue; here the surface is a script whose only behaviour is to wait,
so a session can be held open, SIGKILLed, or made to exit at once.

Everything around that script is production code: the provider subclasses
:class:`~ai_hats.surfaces.claude.provider.ClaudeProvider`, so the session cache
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

#: Provider name the fake surface registers under.
SURFACE = "holdfast"

_PROVIDER_SRC = f'''\
import os
import sys

from ai_hats.surfaces.claude.provider import ClaudeProvider


class HoldfastProvider(ClaudeProvider):
    """The claude surface with its CLI swapped for a script that just waits."""

    @property
    def name(self):
        return "{SURFACE}"

    def get_cli_command(self, args=None):
        return [sys.executable, os.environ["FAKE_SURFACE_HOLD_SCRIPT"], *(args or [])]
'''

#: Exits on its hold OR on being orphaned, so SIGKILLing the ai-hats parent that
#: owns the session leaves nothing running behind it.
_HOLD_SRC = '''\
import os
import sys
import time

sys.stdout.write("fake-surface up\\r\\n")
sys.stdout.flush()
deadline = time.monotonic() + float(os.environ.get("FAKE_SURFACE_HOLD_SECONDS", "0"))
while time.monotonic() < deadline and os.getppid() > 1:
    time.sleep(0.1)
'''

#: ai-hats installs no logging handler anywhere in the tree, so a sweep's INFO
#: record falls to ``logging.lastResort`` (WARNING) and vanishes. Wiring one is
#: the host's job, and the test IS the host — else no reclaim is observable.
_SITECUSTOMIZE = '''\
import logging
import sys

logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(name)s %(message)s")
'''

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

    @property
    def sid(self) -> str:
        return self.cache_dir.name

    @property
    def pid(self) -> int:
        return self.proc.pid

    def tail(self) -> str:
        return self.log.read_text(errors="replace")[-2000:]

    def kill_and_reap(self) -> None:
        """SIGKILL the process group and reap it.

        Reaping is load-bearing, not tidiness: an unreaped child stays a zombie,
        a zombie answers ``os.kill(pid, 0)`` and still holds a ``ps`` row, so its
        owner reads as alive and the staged crash would not be one.
        """
        os.killpg(os.getpgid(self.pid), signal.SIGKILL)
        self.proc.wait(timeout=30)

    def kill_if_running(self) -> None:
        if self.proc.poll() is None:
            self.kill_and_reap()


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

    def start_held(self, name: str, *, role: str = "assistant") -> HeldSession:
        """Launch a session and wait until its cache dir is fully materialized."""
        log = self.tmp / f"fake-surface-{name}.log"
        with log.open("wb") as sink:
            proc = subprocess.Popen(
                self._argv(role),
                cwd=str(self.project),
                env={**self.env, "FAKE_SURFACE_HOLD_SECONDS": HOLD_SECONDS},
                stdin=subprocess.DEVNULL,
                stdout=sink,
                stderr=subprocess.STDOUT,
                # Its own group, so a SIGKILL reaches the whole session.
                start_new_session=True,
            )
        return HeldSession(proc=proc, cache_dir=self._await_cache_dir(proc, log), log=log)

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

    hold_script = tmp_path / "hold.py"
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
