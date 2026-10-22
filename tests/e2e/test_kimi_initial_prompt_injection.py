"""e2e (HATS-2075)

flow:   a developer wrapping kimi with a bare positional prompt
cmds:
    ai-hats -p kimi-stub "e2e probe prompt"
expect: the prompt never reaches the stub's argv (kimi has no positional);
        instead the wrap layer types it into the TUI over the PTY master —
        bracketed-paste blob, then a deferred Enter. A no-prompt run injects
        nothing (positive control).
why:    kimi's CLI errors 'unknown command' on a positional; without the peel +
        injection, `ai-hats -p kimi "..."` silently failed to launch the prompt
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.surfaces]

_REPO_ROOT = Path(__file__).resolve().parents[2]

_SURFACE = "kimi-stub"

_PROVIDER_SRC = f'''\
import os
import sys

from ai_hats.surfaces.kimi.provider import KimiSurface


class KimiStubProvider(KimiSurface):
    """The kimi surface with its CLI swapped for a script that plays TUI."""

    @property
    def name(self):
        return "{_SURFACE}"

    def get_cli_command(self, args=None):
        return [sys.executable, os.environ["KIMI_STUB_SCRIPT"], *(args or [])]

    def readiness_findings(self, environ):
        return []  # the stub needs no real kimi on PATH
'''

#: A minimal TUI: redraws a few times (so the debounce has output to wait out),
#: then reads stdin RAW — as a real TUI would — until the deferred Enter
#: arrives, and dumps both its argv and every byte it received. Self-exits when
#: no input comes, so a no-prompt run never hangs the suite.
_STUB_SRC = """\
import os
import sys
import termios
import time
import tty

sys.stdout.write("kimi-stub boot\\r\\n")
sys.stdout.flush()
time.sleep(0.3)
sys.stdout.write("kimi-stub ready\\r\\n")
sys.stdout.flush()

fd = sys.stdin.fileno()
old = termios.tcgetattr(fd)
tty.setraw(fd)
received = b""
deadline = time.monotonic() + 5.0
try:
    import select

    while time.monotonic() < deadline and not received.endswith(b"\\r"):
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            received += chunk
finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, old)

with open(os.environ["KIMI_STUB_DUMP"], "wb") as fh:
    fh.write(received)
with open(os.environ["KIMI_STUB_ARGV"], "w") as fh:
    fh.write("\\n".join(sys.argv[1:]))
sys.stdout.write("kimi-stub bye\\r\\n")
sys.stdout.flush()
"""


def _install(tmp_project, tmp_path: Path) -> dict[str, str]:
    """Register the stub surface and return the extra env for the run."""
    from _helpers.env import checkout_pythonpath

    plugin = tmp_path / "kimi-stub-plugin"
    plugin.mkdir()
    (plugin / "kimi_stub_provider.py").write_text(_PROVIDER_SRC)
    dist_info = plugin / "kimi_stub_hats-0.1.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: kimi-stub-hats\nVersion: 0.1\n"
    )
    (dist_info / "entry_points.txt").write_text(
        f"[ai_hats.providers]\n{_SURFACE} = kimi_stub_provider:KimiStubProvider\n"
    )
    stub = tmp_path / "kimi_stub.py"
    stub.write_text(_STUB_SRC)
    return {
        "PYTHONPATH": os.pathsep.join([checkout_pythonpath(_REPO_ROOT), str(plugin)]),
        "KIMI_STUB_SCRIPT": str(stub),
        "KIMI_STUB_DUMP": str(tmp_path / "received.bin"),
        "KIMI_STUB_ARGV": str(tmp_path / "argv.txt"),
    }


def test_positional_prompt_is_typed_into_the_kimi_tui(tmp_project, tmp_path: Path) -> None:
    env = _install(tmp_project, tmp_path)

    result = tmp_project.run(
        "-r", "assistant", "-p", _SURFACE, "e2e probe prompt", extra_env=env, timeout=90.0
    ).expect_ok()

    received = (tmp_path / "received.bin").read_bytes()
    assert received == b"\x1b[200~e2e probe prompt\x1b[201~\r"
    argv = (tmp_path / "argv.txt").read_text()
    assert "e2e probe prompt" not in argv
    assert "initial prompt was not delivered" not in result.stderr


def test_no_prompt_means_no_injection(tmp_project, tmp_path: Path) -> None:
    """Positive control: the injector stays inert without a bare positional."""
    env = _install(tmp_project, tmp_path)

    tmp_project.run("-r", "assistant", "-p", _SURFACE, extra_env=env, timeout=90.0).expect_ok()

    assert (tmp_path / "received.bin").read_bytes() == b""
