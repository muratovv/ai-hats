"""E2E: the ai-hats-library data package installs and resolves ALONE (HATS-876/T18).

Real ``uv build`` + install of ONLY ``ai-hats-library`` into a bare venv — no
ai-hats, no other workspace member — then probes the data-only-wheel contract
(review P1 #14): ``files("ai_hats_library")`` is a real dir with
``core/``+``usage/``+``hooks/`` and ``as_file`` round-trips a ``SKILL.md`` read.
Proves the visibility win (drop-in for a non-ai-hats consumer) and that the
package is self-contained (zero deps). Fail-under-revert: if the wheel stops
shipping the layer data, the probe's ``dir`` checks go red.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from _helpers.env import clean_env  # noqa: E402
from _helpers.venv import network_available, venv_unavailable  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PACKAGE_DIR = REPO_ROOT / "packages" / "ai-hats-library"

pytestmark = pytest.mark.install_heavy


def _run(cmd, *, cwd, env, timeout):
    result = subprocess.run(
        cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"{cmd} exited {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


# In-venv probe: the shipped layer tree is reachable and as_file round-trips.
_PROBE = r"""
from importlib.resources import as_file, files
p = files("ai_hats_library")
print("LAYERS", all((p / d).is_dir() for d in ("core", "usage", "hooks")))
skill = next((p / "core" / "skills").iterdir()) / "SKILL.md"
with as_file(skill) as real:
    print("ASFILE", real.is_file() and "---" in real.read_text(encoding="utf-8"))
"""


@pytest.mark.integration
def test_e2e_library_package_installs_alone(tmp_path):
    if not network_available():
        venv_unavailable("uv not on PATH — cannot build/install the library wheel")

    env = clean_env()
    wheeldir = tmp_path / "wheels"

    # Static version + no vcs → build straight from the in-tree package dir
    # (read-only source; output is per-test, no in-tree race).
    _run(["uv", "build", "--wheel", "--out-dir", str(wheeldir), str(PACKAGE_DIR)],
         cwd=tmp_path, env=env, timeout=120)
    wheels = sorted(wheeldir.glob("ai_hats_library-*.whl"))
    assert wheels, f"no ai-hats-library wheel built under {wheeldir}"

    venv = tmp_path / "venv"
    _run(["uv", "venv", "--python", "3.11", str(venv)], cwd=tmp_path, env=env, timeout=120)
    _run(["uv", "pip", "install", "--python", str(venv / "bin" / "python"),
          "--find-links", str(wheeldir), "ai-hats-library"],
         cwd=tmp_path, env=env, timeout=180)

    probe = _run([str(venv / "bin" / "python"), "-c", _PROBE], cwd=tmp_path, env=env, timeout=60)
    assert "LAYERS True" in probe.stdout, f"layer tree not shipped:\n{probe.stdout}"
    assert "ASFILE True" in probe.stdout, f"as_file round-trip failed:\n{probe.stdout}"
