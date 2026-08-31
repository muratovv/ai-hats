#!/usr/bin/env python3
"""Every VCS-tracked file under a package's ``src/`` must reach its wheel.

HATS-1877 / the v0.15.0 release blocker: ``src/ai_hats_library/hooks/consent_gate``
is reached by a symlink from inside ``core/skills/``. The sdist recorded those
six files once, under the symlink, and dropped the real directory — so no
published wheel ever carried ``ai_hats_library.hooks.consent_gate``, which
``ai_hats.consent_wrapper`` imports at module level.

**The build must go through the sdist.** ``uv build <pkg>`` builds the sdist and
then the wheel FROM it, which is what every release workflow here does and what
every published artefact therefore is. ``uv build --wheel`` builds from the tree
instead and cannot see this class at all — measured: the suite's eleven build
call sites all pass ``--wheel``, so it had never once built the artefact the
release publishes. Do not add that flag to make this faster.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

TAG = "[wheel-contents]"

BUILD_TIMEOUT_S = 300


def _tracked_sources(pkg: Path) -> dict[str, bool]:
    """In-wheel path -> whether the repo tracks it as a symlink.

    A symlink cannot be compared by name: the build dereferences it, so the
    wheel carries the target's files under the link's path rather than an entry
    for the link itself.
    """
    src = pkg / "src"
    out = subprocess.run(
        ["git", "ls-files", "-s", "--", str(src.relative_to(REPO_ROOT))],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    tracked: dict[str, bool] = {}
    prefix = f"{src.relative_to(REPO_ROOT).as_posix()}/"
    for line in out.stdout.splitlines():
        if not line.strip():
            continue
        meta, _, path = line.partition("\t")
        mode = meta.split()[0]
        if not path.startswith(prefix):
            continue
        tracked[path[len(prefix) :]] = mode == "120000"
    return tracked


def _wheel_names(pkg: Path, out_dir: Path) -> tuple[set[str], str]:
    """The wheel's namelist, or an empty set and the reason there is none."""
    built = subprocess.run(
        ["uv", "build", str(pkg), "-o", str(out_dir)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=BUILD_TIMEOUT_S,
    )
    if built.returncode != 0:
        tail = (built.stderr or built.stdout).strip().splitlines()
        return set(), f"uv build exited {built.returncode}: {tail[-1] if tail else 'no output'}"
    wheels = sorted(out_dir.glob("*.whl"))
    if not wheels:
        return set(), "uv build produced no wheel"
    with zipfile.ZipFile(wheels[-1]) as zf:
        return set(zf.namelist()), ""


def _missing(tracked: dict[str, bool], names: set[str]) -> list[str]:
    absent = []
    for path, is_symlink in sorted(tracked.items()):
        if path in names:
            continue
        # A dereferenced link is satisfied by the files carried beneath it.
        if is_symlink and any(name.startswith(f"{path}/") for name in names):
            continue
        absent.append(path)
    return absent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "packages",
        nargs="*",
        help="package directories to check (default: every packages/* with a pyproject.toml)",
    )
    args = parser.parse_args(argv)

    if shutil.which("uv") is None:
        print(f"{TAG} SKIPPED: uv is not on PATH — nothing could be built.", file=sys.stderr)
        print(f"{TAG} No package's wheel was checked by this run.", file=sys.stderr)
        return 0

    if args.packages:
        pkgs = [Path(p) if Path(p).is_absolute() else REPO_ROOT / p for p in args.packages]
    else:
        pkgs = sorted(p.parent for p in (REPO_ROOT / "packages").glob("*/pyproject.toml"))
    if not pkgs:
        print(f"{TAG} FAIL: no package found to check.", file=sys.stderr)
        return 1

    failed = False
    for pkg in pkgs:
        name = pkg.name
        tracked = _tracked_sources(pkg)
        if not tracked:
            print(
                f"{TAG} FAIL: {name} tracks no file under src/ — is the layout what this expects?",
                file=sys.stderr,
            )
            failed = True
            continue
        with tempfile.TemporaryDirectory(prefix="wheel-contents-") as tmp:
            names, reason = _wheel_names(pkg, Path(tmp))
            if reason:
                print(f"{TAG} FAIL: {name}: {reason}", file=sys.stderr)
                print(
                    f"{TAG}   (a cold uv cache needs network for the build backend)",
                    file=sys.stderr,
                )
                failed = True
                continue
            absent = _missing(tracked, names)
        if absent:
            failed = True
            print(
                f"{TAG} FAIL: {name}: {len(absent)} tracked file(s) never reached the wheel:",
                file=sys.stderr,
            )
            for path in absent[:20]:
                print(f"{TAG}     {path}", file=sys.stderr)
            if len(absent) > 20:
                print(f"{TAG}     ... and {len(absent) - 20} more", file=sys.stderr)
        else:
            print(f"{TAG} ok: {name} ({len(tracked)} tracked file(s) all present)", file=sys.stderr)

    if failed:
        print(
            f"{TAG} The wheel is built FROM the sdist, as every release here builds it. "
            f"A file the sdist drops is a file no user ever gets.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
