"""E2E: the remote channel resolves an ai-hats-core with .migrations (HATS-943/937).

Build the ai-hats wheel from the working tree, install it into a **fresh** venv
so `ai-hats-core` resolves from PyPI (not the workspace) — the one config no
other test exercises. The probe import is what raised `ModuleNotFoundError:
ai_hats_core.migrations` under the pre-fix `>=0.3.0` pin. Fail-under-revert: pin
`>=0.3.0` → install resolves a core without `.migrations` → import raises. Real
`uv build` + `uv pip install` (PyPI) → `@install_heavy`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.install_heavy]

REPO_ROOT = Path(__file__).resolve().parents[2]

# The exact import chain that crashed pre-fix.
_PROBE = "import ai_hats_core.migrations; import ai_hats.migrations; print('remote-ok')"


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True)


def test_remote_style_install_resolves_core_with_migrations(tmp_path: Path) -> None:
    # Skip until ai-hats-library (a T18 Requires-Dist) is on PyPI — a from-index
    # install can't resolve it before then. Resolver liveness = the gate. HATS-988.
    from ai_hats.channel import ChannelResolveError, fetch_latest_stable_version

    # Both T18 Requires-Dist deps must be on PyPI before a from-index install can
    # resolve; ai-hats-rack joined the wheel's deps (HATS-1040) and is unpublished.
    for _dep in ("ai-hats-library", "ai-hats-rack"):
        try:
            fetch_latest_stable_version(f"https://pypi.org/pypi/{_dep}/json")
        except ChannelResolveError as exc:
            pytest.skip(f"{_dep} not yet published on PyPI ({exc})")

    dist = tmp_path / "dist"
    _run(["uv", "build", "--wheel", str(REPO_ROOT), "-o", str(dist)], cwd=tmp_path)
    wheel = next(dist.glob("ai_hats-*.whl"))

    venv = tmp_path / "venv"
    _run(["uv", "venv", "--python", "3.11", str(venv)], cwd=tmp_path)
    py = venv / "bin" / "python"

    # cwd=tmp_path (outside the repo) so uv resolves deps from the index, not the
    # workspace [tool.uv.sources] — i.e. core comes from PyPI, like a real heal.
    _run(["uv", "pip", "install", "--python", str(py), str(wheel)], cwd=tmp_path)

    out = _run([str(py), "-c", _PROBE], cwd=tmp_path)
    assert "remote-ok" in out.stdout
