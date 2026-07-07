"""Published-version drift guard (HATS-921).

A ``packages/*`` member whose local version is already on PyPI must match the
published wheel byte-for-byte — else resolvers serve the stale index wheel to
fresh installs (equal-version find-links loses) and new imports crash.
Editing a published package REQUIRES a bump. Integrator exempt (scm-dynamic).
"""

from __future__ import annotations

import io
import json
import tomllib
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.integration


def _member_pyprojects() -> list[Path]:
    return sorted(REPO_ROOT.glob("packages/*/pyproject.toml"))


def _fetch(url: str, timeout: int) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 — https only
            return r.read()
    except urllib.error.HTTPError:
        raise
    except (urllib.error.URLError, TimeoutError) as e:
        pytest.skip(f"PyPI unreachable: {e}")


@pytest.mark.parametrize("pyproject", _member_pyprojects(), ids=lambda p: p.parent.name)
def test_published_version_matches_source(pyproject: Path) -> None:
    project = tomllib.loads(pyproject.read_text())["project"]
    name, version = project["name"], project["version"]

    try:
        index = json.loads(_fetch(f"https://pypi.org/pypi/{name}/json", timeout=10))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return  # never published — nothing to drift against
        raise
    files = index.get("releases", {}).get(version)
    if not files:
        return  # this version not published — local-only, resolvers prefer it

    wheel_url = next((f["url"] for f in files if f["filename"].endswith(".whl")), None)
    assert wheel_url, f"{name} {version} is published without a wheel"
    wheel = zipfile.ZipFile(io.BytesIO(_fetch(wheel_url, timeout=30)))

    (src_pkg,) = [
        p
        for p in (pyproject.parent / "src").iterdir()
        if p.is_dir() and (p / "__init__.py").exists()
    ]
    local = {
        f"{src_pkg.name}/{p.relative_to(src_pkg).as_posix()}": p.read_bytes()
        for p in src_pkg.rglob("*.py")
    }
    published = {
        n: wheel.read(n)
        for n in wheel.namelist()
        if n.startswith(f"{src_pkg.name}/") and n.endswith(".py")
    }

    missing_or_extra = sorted(set(local) ^ set(published))
    changed = sorted(n for n in set(local) & set(published) if local[n] != published[n])
    assert not (missing_or_extra or changed), (
        f"{name} {version} is published on PyPI but the local source differs "
        f"(files: {missing_or_extra or '-'}; content: {changed or '-'}). "
        f"Resolvers will serve the STALE published wheel to fresh installs — "
        f"bump the version in {pyproject.relative_to(REPO_ROOT)}."
    )
