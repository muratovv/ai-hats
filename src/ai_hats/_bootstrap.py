"""Startup self-heal for missing runtime dependencies (HATS-213).

Closes the bootstrap chicken-and-egg that survived HATS-207: a user upgrading
from a pre-HATS-207 wheel runs `ai-hats self update` from the OLD in-memory code
(which still passes ``--no-deps``); pip installs the new wheel without the
new declared deps; the next `ai-hats` invocation crashes with
``ModuleNotFoundError``. This module detects that state on every CLI startup
and on every ``ai-hats self update`` and self-heals it.

Stdlib-only on purpose — must not import anything from the project, since
the project itself is what may be missing dependencies.

Two entry points:

* :func:`bootstrap_or_die` — called first in ``cli.main()``. Detects missing
  runtime deps; uv-installs them; ``os.execv`` re-execs the same command
  in a fresh interpreter so freshly-installed modules are importable.
* :func:`verify_after_install` — called via ``python -m ai_hats._bootstrap
  verify`` from ``cli.maintenance.update()`` as a stage-2 check inside a
  fresh subprocess. Heals without re-exec (we are already exiting).

POSIX-only re-exec semantics: ``os.execv`` works on Windows but the parent
shell does not wait for the replaced process. ai-hats is effectively
Unix-only (PTY-dependent); Windows users will see the rescue command and
must run it manually.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import re
import subprocess
import sys

# PEP 503 normalised dist name → import name. Only required when the two
# differ; auto-sync keeps the rest of pyproject.toml under protection
# without touching this file.
_IMPORT_NAME_OVERRIDES: dict[str, str] = {
    "pyyaml": "yaml",
}

# PEP 508 — strip everything after the first space, version specifier,
# environment marker, or extras bracket to get the bare distribution name.
_PEP508_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")


def _normalise(dist: str) -> str:
    """PEP 503 — lowercase, runs of [-_.] collapsed to a single hyphen."""
    return re.sub(r"[-_.]+", "-", dist.strip().lower())


def _parse_requirement(req: str) -> str | None:
    """Extract distribution name from a PEP 508 requirement line.

    Returns ``None`` for env-marker-only entries that don't apply or for
    requirements we can't parse (defensive — never crash bootstrap).
    """
    # Env marker filtering: 'pkg ; extra == "dev"' should not be treated as
    # required at runtime. importlib.metadata.requires() returns extras as
    # ``foo; extra == "dev"`` — skip those.
    if ";" in req:
        head, marker = req.split(";", 1)
        if "extra" in marker:
            return None
        req = head
    m = _PEP508_NAME_RE.match(req.strip())
    return m.group(0) if m else None


def expected_runtime_deps() -> list[tuple[str, str]]:
    """Return ``[(dist_name, import_name), ...]`` for runtime deps of ai-hats.

    Source of truth is ``importlib.metadata.requires("ai-hats")`` — auto-
    syncs with ``pyproject.toml`` so any new dep is protected without
    touching this module.
    """
    try:
        raw = importlib.metadata.requires("ai-hats") or []
    except importlib.metadata.PackageNotFoundError:
        return []

    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for req in raw:
        dist = _parse_requirement(req)
        if not dist:
            continue
        norm = _normalise(dist)
        if norm in seen:
            continue
        seen.add(norm)
        import_name = _IMPORT_NAME_OVERRIDES.get(norm, norm.replace("-", "_"))
        out.append((dist, import_name))
    return out


def find_missing_runtime_deps() -> list[str]:
    """List distribution names whose import is unavailable in this interpreter."""
    missing: list[str] = []
    for dist, import_name in expected_runtime_deps():
        try:
            spec = importlib.util.find_spec(import_name)
        except (ValueError, ModuleNotFoundError):
            spec = None
        if spec is None:
            missing.append(dist)
    return missing


def attempt_self_heal(missing: list[str]) -> bool:
    """Run ``uv pip install`` for the missing distributions. True on exit 0.

    HATS-763: uv is the single engine — no pip fallback (D2). ``--python
    sys.executable`` targets THIS interpreter (B1); a uv venv ships no pip, so
    ``python -m pip`` would not even exist here. A missing uv binary raises
    FileNotFoundError (OSError) → caught → returns False, and the caller prints
    the rescue command + exits.
    """
    if not missing:
        return True
    cmd = ["uv", "pip", "install", "--python", sys.executable, *missing]
    try:
        result = subprocess.run(cmd, check=False)
    except OSError:
        return False
    return result.returncode == 0


def _rescue_command(missing: list[str]) -> str:
    quoted = " ".join(f"'{m}'" for m in missing)
    return f"uv pip install --python {sys.executable} {quoted}"


def bootstrap_or_die() -> None:
    """Detect missing runtime deps; uv-install + re-exec, or die loudly.

    Called as the very first action in :func:`ai_hats.cli.main`. Side effects:
    prints to stderr, runs uv in a subprocess, and on success replaces the
    current process via :func:`os.execv` so freshly-installed modules become
    importable for the actual command the user invoked.
    """
    missing = find_missing_runtime_deps()
    if not missing:
        return

    sys.stderr.write(
        f"ai-hats: missing runtime deps {missing}; healing via uv…\n"
        f"  manual command if this fails: {_rescue_command(missing)}\n"
    )
    sys.stderr.flush()

    if not attempt_self_heal(missing):
        sys.stderr.write(
            "ai-hats: self-heal failed. Run the manual command above, then retry.\n"
        )
        sys.exit(1)

    # Re-exec a fresh interpreter so that freshly-installed modules can be
    # imported. argv[0] becomes the interpreter; the rest is whatever the
    # user originally invoked.
    os.execv(sys.executable, [sys.executable, "-m", "ai_hats", *sys.argv[1:]])


def verify_after_install() -> int:
    """Stage-2 verify after `ai-hats self update`. No re-exec; returns exit code.

    Designed to be run via ``python -m ai_hats._bootstrap verify`` in a
    fresh subprocess from :func:`ai_hats.cli.maintenance.update`. Because
    it's a fresh process, it reads the just-installed on-disk code, so any
    new ``EXPECTED_DEPS`` in this file are honoured immediately.
    """
    missing = find_missing_runtime_deps()
    if not missing:
        return 0
    sys.stderr.write(
        f"ai-hats: post-install verify found missing deps {missing}; healing…\n"
    )
    if attempt_self_heal(missing):
        # Re-check — uv can succeed but install nothing useful in pathological
        # cases (e.g. wheel for wrong platform). Trust but verify.
        still_missing = find_missing_runtime_deps()
        if not still_missing:
            return 0
        sys.stderr.write(
            f"ai-hats: deps still missing after uv install: {still_missing}\n"
        )
    sys.stderr.write(
        f"  manual command: {_rescue_command(missing)}\n"
    )
    return 1


def _main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "verify":
        return verify_after_install()
    sys.stderr.write("usage: python -m ai_hats._bootstrap verify\n")
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
