#!/usr/bin/env python3
"""Fail when a workspace package's source outgrew its published version (HATS-943).

Invariant per `packages/*` member: if its `src/**` changed vs the base ref, its
`pyproject.toml` version MUST be strictly greater than the latest on PyPI — else
the published wheel diverges from source and the remote channel resolves a stale
`core` (`ModuleNotFoundError`, the HATS-923/937 skew class). `evaluate` is the
pure, unit-tested decision; git/PyPI/pyproject reads are thin adapters.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from packaging.version import InvalidVersion, Version


@dataclass(frozen=True)
class Verdict:
    package: str
    ok: bool
    reason: str


def evaluate(
    package: str,
    src_ver: Version | None,
    pypi_ver: Version | None,
    src_changed: bool,
) -> Verdict:
    """Pure gate decision for one package. See module docstring for the invariant."""
    if src_ver is None:
        return Verdict(package, True, "skipped (no static version)")
    if not src_changed:
        return Verdict(package, True, f"src unchanged (v{src_ver})")
    if pypi_ver is None:
        return Verdict(package, True, f"never published (v{src_ver})")
    if src_ver > pypi_ver:
        return Verdict(package, True, f"v{src_ver} > published v{pypi_ver}")
    return Verdict(
        package,
        False,
        f"src changed but v{src_ver} <= published v{pypi_ver} — bump "
        f"packages/{package.replace('_', '-')}/pyproject.toml above {pypi_ver} "
        f"(then publish), else the remote channel resolves a stale wheel",
    )


def _git(args: list[str], cwd: Path) -> str:
    # Fixed argv, no shell, no untrusted input — S603/S607 are inapplicable here.
    return subprocess.run(  # noqa: S603, S607
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def source_meta(pyproject_path: Path) -> tuple[str, Version | None]:
    """Return (project name, static Version | None) from a package pyproject."""
    project = tomllib.loads(pyproject_path.read_text()).get("project", {})
    name = project.get("name", pyproject_path.parent.name)
    raw = project.get("version")
    if raw is None:  # dynamic (setuptools-scm) or missing → not our concern
        return name, None
    try:
        return name, Version(raw)
    except InvalidVersion:
        return name, None


def latest_pypi_version(name: str, *, fetch=None) -> Version | None:
    """Latest version on PyPI, or None if the project is unpublished (404)."""
    fetch = fetch or _http_get_json
    try:
        payload = fetch(f"https://pypi.org/pypi/{name}/json")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    try:
        return Version(payload["info"]["version"])
    except (KeyError, InvalidVersion):
        return None


def _http_get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 — fixed https host
        return json.loads(resp.read().decode())


def changed_packages(base_ref: str, packages_dir: Path, repo_root: Path) -> set[str]:
    """Package dir names whose ``src/**`` changed between base_ref and HEAD."""
    out = _git(["diff", "--name-only", f"{base_ref}...HEAD", "--", packages_dir.name], repo_root)
    changed: set[str] = set()
    prefix = packages_dir.name + "/"
    for line in out.splitlines():
        if not line.startswith(prefix):
            continue
        parts = line[len(prefix) :].split("/")
        if len(parts) >= 2 and parts[1] == "src":
            changed.add(parts[0])
    return changed


def run(repo_root: Path, base_ref: str, *, fetch=None) -> list[Verdict]:
    packages_dir = repo_root / "packages"
    changed = changed_packages(base_ref, packages_dir, repo_root)
    verdicts: list[Verdict] = []
    for pkg_dir in sorted(p for p in packages_dir.iterdir() if p.is_dir()):
        pyproject = pkg_dir / "pyproject.toml"
        if not pyproject.exists():
            continue
        name, src_ver = source_meta(pyproject)
        pypi_ver = latest_pypi_version(name, fetch=fetch) if src_ver is not None else None
        verdicts.append(evaluate(pkg_dir.name, src_ver, pypi_ver, pkg_dir.name in changed))
    return verdicts


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    base_ref = argv[0] if argv else "origin/master"
    repo_root = Path(_git(["rev-parse", "--show-toplevel"], Path.cwd()).strip())
    verdicts = run(repo_root, base_ref)
    failed = [v for v in verdicts if not v.ok]
    for v in verdicts:
        print(f"[version-skew] {'FAIL' if not v.ok else 'ok'}: {v.package} — {v.reason}",
              file=sys.stderr)
    if failed:
        print(f"[version-skew] {len(failed)} package(s) skewed vs PyPI — see above.",
              file=sys.stderr)
        return 1
    print("[version-skew] all workspace packages ahead of / clean vs PyPI.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
