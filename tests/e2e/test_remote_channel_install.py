"""e2e (HATS-943, HATS-988, HATS-1717)

flow:   a maintainer checks that a released ai-hats installs the way a user's heal
        would install it — first-party packages resolved from the index, never from
        the workspace sources this repo carries
cmds:
    uv build --wheel . -o dist          # the wheel a user would get
    uv venv --python 3.13 venv          # a fresh interpreter, outside the repo
    uv pip install --python venv/bin/python dist/ai_hats-0.0.0-py3-none-any.whl
expect: the installed wheel imports `ai_hats_core.migrations` and `ai_hats.migrations`;
        when a first-party pin is not yet visible to the resolver the test skips
        carrying the resolver's OWN refusal, never a second oracle's opinion
why:    a heal installs from the index, so a pin that resolves only against the
        workspace ships broken to every user. The skip has to be asked of `uv`:
        the version comparison it replaced read `pypi.org/pypi/<name>/json` while
        the install resolved `pypi.org/simple/<name>/`, whose compressed variant
        was hours stale, and the tier went red for a reason the guard was written
        to excuse (HATS-1717)"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement

pytestmark = [pytest.mark.integration, pytest.mark.install_heavy, pytest.mark.install]

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


def _venv(tmp_path: Path) -> Path:
    venv = tmp_path / "venv"
    _run(["uv", "venv", "--python", "3.13", str(venv)], cwd=tmp_path)
    return venv / "bin" / "python"


def _unresolvable(py: Path, req: Requirement, cwd: Path) -> str:
    """The resolver's own answer on one pin: its refusal, or ``""`` when it resolves.

    HATS-1717: asked through `uv`, never a second oracle. The pin-vs-PyPI comparison
    this replaces read `pypi.org/pypi/<name>/json` while the install resolved
    `pypi.org/simple/<name>/`, and the two disagreed for hours — the simple page is
    cached per `Accept-Encoding` (`Vary`), and the compressed variant uv asks for
    was stale, so the guard saw a version the resolver did not.
    """
    done = subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["uv", "pip", "install", "--dry-run", "--python", str(py), str(req)],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return "" if done.returncode == 0 else done.stderr.strip()


def test_remote_style_install_resolves_core_with_migrations(tmp_path: Path) -> None:
    # cwd=tmp_path (outside the repo) so uv resolves deps from the index, not the
    # workspace [tool.uv.sources] — i.e. core comes from PyPI, like a real heal.
    py = _venv(tmp_path)

    # Resolver liveness is the gate (HATS-988) — a first-party pin the resolver
    # cannot see is not a resolver defect, so skip. Not yet published, or bumped
    # ahead of its publish (normal pre-merge: a bump publishes on push to master,
    # HATS-943). Asked pin by pin: if every pin resolves alone and the wheel still
    # fails, that IS the defect this test exists for, and it stays red.
    for req in _first_party_pins():
        refusal = _unresolvable(py, req, tmp_path)
        if refusal:
            pytest.skip(f"{req} is not resolvable from the index uv reads:\n{refusal}")

    dist = tmp_path / "dist"
    _run(["uv", "build", "--wheel", str(REPO_ROOT), "-o", str(dist)], cwd=tmp_path)
    wheel = next(dist.glob("ai_hats-*.whl"))

    _run(["uv", "pip", "install", "--python", str(py), str(wheel)], cwd=tmp_path)

    out = _run([str(py), "-c", _PROBE], cwd=tmp_path)
    assert "remote-ok" in out.stdout


def test_the_pin_probe_is_the_resolvers_own_answer(tmp_path: Path) -> None:
    """A pin no index can satisfy refuses; an ancient published one resolves.

    Fail-under-revert, measured: make `_unresolvable` return `""` unconditionally
    → this goes red, and with it the skip that keeps the test above honest.
    """
    py = _venv(tmp_path)

    assert _unresolvable(py, Requirement("ai-hats-library==999.0.0"), tmp_path), (
        "a version that cannot exist must come back as the resolver's refusal"
    )
    assert not _unresolvable(py, Requirement("ai-hats-library>=0.3.0"), tmp_path), (
        "a floor every published version clears must resolve"
    )
