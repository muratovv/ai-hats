"""E2E gate for the integrator wheel (HATS-861 hatchling migration; HATS-876/T18).

Real ``uv build`` + by-name install of the root ``ai-hats`` wheel. R2:
``__version__`` keeps the scm string format + PEP-440 parses. R3 (T18): the
library is a SEPARATE dep — the wheel drops the ``ai_hats/library/`` force-include
and declares ``Requires-Dist: ai-hats-library``; once both install,
``files("ai_hats_library")`` is a real dir with ``core/``+``usage/``. Fail-under-
revert: dropping the pin (or re-adding the force-include) breaks R3; dropping the
``vcs version-file`` hook strips ``_version.py`` (R2).
"""

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

import pytest

from _helpers.env import clean_env  # noqa: E402
from _helpers.repo_src import build_src  # noqa: E402
from _helpers.venv import network_available, venv_unavailable  # noqa: E402
from _helpers.workspace import build_workspace_member_wheels  # noqa: E402
from ai_hats.paths import ENV_AI_HATS_VENV

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# HATS-678/771: real uv build + install at call time → capped via conftest.INSTALL_HEAVY_GROUPS.
pytestmark = pytest.mark.install_heavy


def _run(cmd, *, cwd, env, timeout, expect_exit=0):
    result = subprocess.run(
        cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout,
    )
    if expect_exit is not None and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _wheel_version(wheel: Path) -> str:
    """Canonical version from the wheel's METADATA (NOT the filename — the wheel
    filename escapes a PEP 440 local segment ``+g<sha>`` to ``_g<sha>``, which is
    not a valid ``==`` specifier)."""
    with zipfile.ZipFile(wheel) as zf:
        meta_name = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
        meta = zf.read(meta_name).decode()
    for line in meta.splitlines():
        if line.startswith("Version:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"no Version in {wheel} METADATA")


# In-venv probe: files("ai_hats_library") is a real on-disk dir (R3/T18) +
# __version__ is scm-format and PEP-440 parseable (R2). Markers asserted by the test.
_PROBE = r"""
import importlib.resources as res
from pathlib import Path
import re
from packaging.version import Version
import ai_hats

p = res.files("ai_hats_library")
real = isinstance(p, Path) and (p / "core").is_dir() and (p / "usage").is_dir()
print("LIB", type(p).__name__, real)

v = ai_hats.__version__
Version(v)  # raises if not PEP 440
print("VER", v, bool(re.match(r"^\d+\.\d+\.\d+", v)))
"""


@pytest.mark.integration
def test_e2e_integrator_wheel_build(tmp_path):
    if not network_available():
        venv_unavailable("uv not on PATH — cannot build/install the integrator wheel")

    env = clean_env()  # HATS-685: drop inherited PYTHONPATH/redirect vars
    env.pop(ENV_AI_HATS_VENV, None)  # never leak from outer test runs

    # 1. Build the integrator wheel from a per-worker private clone (no in-tree race).
    src = build_src(REPO_ROOT)
    wheeldir = tmp_path / "wheels"
    _run(["uv", "build", "--wheel", "--out-dir", str(wheeldir), str(src)],
         cwd=tmp_path, env=env, timeout=180)
    wheels = sorted(wheeldir.glob("ai_hats-*.whl"))
    assert wheels, f"no ai-hats wheel built under {wheeldir}"
    wheel = wheels[0]
    version = _wheel_version(wheel)

    # 2. Wheel CONTENT gate (T18) — the library is a SEPARATE dep now: the wheel
    #    must NOT ship ai_hats/library/ and MUST declare Requires-Dist on it. The
    #    vcs hook still emits _version.py.
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
        meta_name = next(n for n in names if n.endswith(".dist-info/METADATA"))
        metadata = zf.read(meta_name).decode()
    assert not any(n.startswith("ai_hats/library/") for n in names), (
        "integrator wheel still ships ai_hats/library/ — force-include not dropped (T18)"
    )
    assert any(
        line.startswith("Requires-Dist:") and "ai-hats-library" in line
        for line in metadata.splitlines()
    ), "integrator wheel does not depend on ai-hats-library (T18 pin missing)"
    assert "ai_hats/_version.py" in names, (
        "wheel missing ai_hats/_version.py — vcs version-file hook broken"
    )

    # 3. Real by-name install into a fresh venv (member wheels satisfy the deps).
    build_workspace_member_wheels(src, wheeldir, env)
    venv = tmp_path / "venv"
    _run(["uv", "venv", "--python", "3.11", str(venv)], cwd=tmp_path, env=env, timeout=120)
    _run(["uv", "pip", "install", "--python", str(venv / "bin" / "python"),
          "--find-links", str(wheeldir), f"ai-hats=={version}"],
         cwd=tmp_path, env=env, timeout=300)
    py = venv / "bin" / "python"
    assert py.is_file(), "venv python missing after by-name install"

    # 4. R3 + R2 probe inside the installed venv.
    probe = _run([str(py), "-c", _PROBE], cwd=tmp_path, env=env, timeout=60)
    assert "LIB PosixPath True" in probe.stdout, (
        f"files('ai_hats_library') did not resolve to a real dir with core+usage:\n{probe.stdout}"
    )
    assert f"VER {version} True" in probe.stdout, (
        f"__version__ not scm-format / not the built version:\n{probe.stdout}"
    )

    # 5. CLI runs.
    cli = _run([str(py), "-m", "ai_hats", "--version"], cwd=tmp_path, env=env, timeout=60)
    assert "version" in cli.stdout.lower(), f"unexpected --version output:\n{cli.stdout}"
