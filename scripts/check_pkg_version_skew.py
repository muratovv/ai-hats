#!/usr/bin/env python3
"""Fail when a workspace package's source outgrew its published version (HATS-943).

Invariant per `packages/*` member: if its `src/**` changed vs the base ref, that
same diff MUST carry a version bump — else the published wheel diverges and the
remote channel resolves a stale `core` (the HATS-923/937 skew class). Already
ahead of PyPI passes too; BEHIND PyPI fails on its own terms. The bump half is
asked of git, never of PyPI: master's push also triggers `release-packages.yml`,
so seconds after a correct bump lands the published version EQUALS the tree's
(HATS-1957). `evaluate` is the pure decision; the reads are thin adapters.
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
    *,
    base_ver: Version | None = None,
) -> Verdict:
    """Pure gate decision for one package. See module docstring for the invariant.

    ``base_ver`` is the version at the diff base — the offline half of the
    verdict. ``None`` means it could not be read there, and the decision falls
    back to the strict comparison against PyPI alone.
    """
    if src_ver is None:
        return Verdict(package, True, "skipped (no static version)")
    if not src_changed:
        return Verdict(package, True, f"src unchanged (v{src_ver})")
    if pypi_ver is None:
        return Verdict(package, True, f"never published (v{src_ver})")
    if src_ver > pypi_ver:
        return Verdict(package, True, f"v{src_ver} > published v{pypi_ver}")
    if src_ver < pypi_ver:
        return Verdict(
            package,
            False,
            f"the tree is behind PyPI — v{src_ver} < published v{pypi_ver}; a "
            f"published wheel outranks this checkout, so the version here was "
            f"lowered or a publish ran from another tree",
        )
    if base_ver is not None and base_ver < src_ver:
        return Verdict(
            package,
            True,
            f"bumped v{base_ver} -> v{src_ver} in this change; PyPI is at "
            f"v{pypi_ver} because the same push published it",
        )
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
    return project.get("name", pyproject_path.parent.name), _static_version(project)


def _static_version(project: dict) -> Version | None:
    raw = project.get("version")
    if raw is None:  # dynamic (setuptools-scm) or missing → not our concern
        return None
    try:
        return Version(raw)
    except InvalidVersion:
        return None


def version_at(ref: str, rel_path: str, repo_root: Path) -> Version | None:
    """Static version of the pyproject at ``rel_path`` as of ``ref``.

    ``None`` when the file is not there (a package added by this very diff) or
    carries no static version — both mean "no base version to compare against".
    """
    proc = subprocess.run(  # noqa: S603, S607 — fixed argv, no shell
        ["git", "show", f"{ref}:{rel_path}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return _static_version(tomllib.loads(proc.stdout).get("project", {}))


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
    req = urllib.request.Request(url, headers={"Accept": "application/json"})  # noqa: S310 — the one caller builds an https PyPI URL
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


def resolve_base(base_ref: str, repo_root: Path) -> str | None:
    """Return the merge-base of base_ref and HEAD, or None if there is none.

    The COMMIT, not the ref that named it: the diff and the base-version read
    below must span the same two trees, and a ref can move between them.
    """
    try:
        proc = subprocess.run(
            ["git", "merge-base", base_ref, "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
        return None
    except Exception:  # silent-ok: no usable base ref is None by contract
        return None


def run(repo_root: Path, base_ref: str, *, fetch=None) -> list[Verdict]:
    packages_dir = repo_root / "packages"
    changed = changed_packages(base_ref, packages_dir, repo_root)
    verdicts: list[Verdict] = []
    # One level deep on purpose; tests/test_packages_flat_layout.py keeps that true.
    for pkg_dir in sorted(p for p in packages_dir.iterdir() if p.is_dir()):
        pyproject = pkg_dir / "pyproject.toml"
        if not pyproject.exists():
            continue
        name, src_ver = source_meta(pyproject)
        pypi_ver = latest_pypi_version(name, fetch=fetch) if src_ver is not None else None
        base_ver = version_at(
            base_ref, f"{packages_dir.name}/{pkg_dir.name}/pyproject.toml", repo_root
        )
        verdicts.append(
            evaluate(
                pkg_dir.name,
                src_ver,
                pypi_ver,
                pkg_dir.name in changed,
                base_ver=base_ver,
            )
        )
    return verdicts


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    base_ref = argv[0] if argv else "origin/master"
    repo_root = Path(_git(["rev-parse", "--show-toplevel"], Path.cwd()).strip())
    resolved = resolve_base(base_ref, repo_root)
    if resolved is None:
        print(
            f"[version-skew] base unusable ({base_ref}) — skipping git diff check; "
            f"deferring to tests/test_package_version_drift.py",
            file=sys.stderr,
        )
        return 0
    verdicts = run(repo_root, resolved)
    failed = [v for v in verdicts if not v.ok]
    for v in verdicts:
        print(
            f"[version-skew] {'FAIL' if not v.ok else 'ok'}: {v.package} — {v.reason}",
            file=sys.stderr,
        )
    if failed:
        print(
            f"[version-skew] {len(failed)} package(s) skewed vs PyPI — see above.", file=sys.stderr
        )
        return 1
    print("[version-skew] every changed workspace package carries its bump.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
