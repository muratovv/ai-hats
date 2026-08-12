"""e2e (HATS-943)

flow:   a developer initializing a project configured with remote git harness channel
cmds:
    ai-hats self init --channel remote
expect: project config sets remote harness channel and self update fetches updates from
        remote git repo
why: without remote channel support, production installations cannot update directly
     from remote git repos"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement

pytestmark = [pytest.mark.integration, pytest.mark.install_heavy]

REPO_ROOT = Path(__file__).resolve().parents[2]

# The exact import chain that crashed pre-fix.
_PROBE = "import ai_hats_core.migrations; import ai_hats.migrations; print('remote-ok')"


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True)


def _first_party_pins() -> list[Requirement]:
    """The wheel's own ``ai-hats-*`` requirements, as the resolver sees them."""
    with (REPO_ROOT / "pyproject.toml").open("rb") as f:
        deps = tomllib.load(f)["project"]["dependencies"]
    reqs = [Requirement(d) for d in deps]
    return [r for r in reqs if r.name.startswith("ai-hats")]


def test_remote_style_install_resolves_core_with_migrations(tmp_path: Path) -> None:
    # Resolver liveness is the gate (HATS-988) — an unsatisfiable first-party pin
    # is not a resolver defect, so skip. Absent from PyPI, or bumped ahead of its
    # publish (normal pre-merge: a bump publishes on push to master, HATS-943).
    from ai_hats.channel import ChannelResolveError, fetch_latest_stable_version

    for req in _first_party_pins():
        try:
            published = fetch_latest_stable_version(f"https://pypi.org/pypi/{req.name}/json")
        except ChannelResolveError as exc:
            pytest.skip(f"{req.name} not yet published on PyPI ({exc})")
        if not req.specifier.contains(published):
            pytest.skip(
                f"{req.name}{req.specifier} is unsatisfiable from PyPI (latest "
                f"published {published}) — the bump publishes on merge to master"
            )

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
