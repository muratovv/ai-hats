"""Startup self-heal for runtime and editable-install metadata drift.

Closes the bootstrap chicken-and-egg that survived HATS-207: a user upgrading
from a pre-HATS-207 wheel runs `ai-hats self update` from the OLD in-memory code
(which still passes ``--no-deps``); pip installs the new wheel without the
new declared deps; the next `ai-hats` invocation crashes with
``ModuleNotFoundError``. This module detects that state on every CLI startup
and on every ``ai-hats self update`` and self-heals it.

Stdlib-only on purpose — must not import anything from the project, since
the project itself is what may be missing dependencies.

Two entry points:

* :func:`bootstrap_or_die` — called first in ``__main__.main()``, ahead of the
  ``ai_hats.cli`` import it protects (HATS-1368). Detects missing runtime deps
  and stale editable step entry points; repairs the install; ``os.execv``
  re-execs the same command in a fresh interpreter.
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
import json
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

# Importing these proves the installed tree agrees with itself: the CLI entry
# the launcher exec's into, and the assembler it reaches for first (HATS-1116).
_INTEGRITY_MODULES = ("ai_hats.cli", "ai_hats.assembler")
_STEP_ENTRY_POINT_GROUP = "ai_hats.steps"
_PROVIDER_ENTRY_POINT_GROUP = "ai_hats.providers"
# Groups ai-hats declares for ITSELF. Both reach a process only through installed
# metadata, so on an editable checkout both go stale the same way (HATS-1810).
_FIRST_PARTY_ENTRY_POINT_GROUPS = (_STEP_ENTRY_POINT_GROUP, _PROVIDER_ENTRY_POINT_GROUP)


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


def _editable_source_dir() -> str | None:
    """Filesystem path of the editable checkout backing this ai-hats install.

    Only a local editable install has one. PEP 610 marks it ``dir_info.editable``;
    a wheel from PyPI carries ``archive_info`` instead, so this returns ``None``
    there and every caller keeps its pre-HATS-1367 behaviour. ``None`` too when
    the metadata is absent or malformed, or the recorded checkout is gone —
    absent, unreadable and foreign all mean the same thing here: nothing local to
    re-point at. POSIX-only path handling, matching this module's re-exec contract.
    """
    from urllib.parse import unquote, urlparse

    try:
        raw = importlib.metadata.distribution("ai-hats").read_text("direct_url.json")
        data = json.loads(raw or "")
        if not data["dir_info"]["editable"]:
            return None
        path = unquote(urlparse(data["url"]).path)
    except Exception:  # silent-ok: absent, unreadable and foreign all mean None here
        return None
    return path if os.path.isdir(path) else None


def _live_pyproject_deps(src: str) -> list[str] | None:
    """``[project].dependencies`` from the checkout at ``src``; ``None`` on any snag.

    ``src`` is always a local editable checkout — :func:`_editable_source_dir`
    returns nothing else — so this never reaches for a pyproject.toml inside an
    installed PyPI package, where sdists may ship one and wheels never do. A
    checkout without a readable ``[project].dependencies`` (setup.py-only, or
    moved out from under us) falls back to METADATA like a wheel install.
    """
    import tomllib

    try:
        with open(os.path.join(src, "pyproject.toml"), "rb") as fh:
            deps = tomllib.load(fh)["project"]["dependencies"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(deps, list):
        return None
    return [d for d in deps if isinstance(d, str)]


def _live_entry_points(src: str, group: str) -> list[tuple[str, str]] | None:
    """One group's declarations from an editable checkout; ``None`` when unreadable."""
    import tomllib

    try:
        with open(os.path.join(src, "pyproject.toml"), "rb") as fh:
            declared = tomllib.load(fh)["project"]["entry-points"][group]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(declared, dict) or not all(
        isinstance(name, str) and isinstance(value, str) for name, value in declared.items()
    ):
        return None
    return sorted(declared.items())


def find_editable_entry_point_drift() -> list[str]:
    """Report stale first-party entry-point metadata for the active editable checkout.

    Both groups, because both are how ai-hats reaches its own code: a step the
    loader resolves by id, and a surface the registry looks up by name. Neither
    has a fallback table by design (ADR-0026 D3), so stale metadata does not
    degrade — it removes the thing (HATS-1810, HATS-1826).
    """
    src = _editable_source_dir()
    if src is None:
        return []
    dist = importlib.metadata.distribution("ai-hats")
    drift = []
    for group in _FIRST_PARTY_ENTRY_POINT_GROUPS:
        live = _live_entry_points(src, group)
        if live is None:
            continue
        installed = sorted((ep.name, ep.value) for ep in dist.entry_points if ep.group == group)
        if installed != live:
            drift.append(
                f"{group} metadata is stale for editable checkout {src}: "
                f"live={live!r}, installed={installed!r}"
            )
    return drift


def _declared_requirements() -> list[str]:
    """Requirement lines ai-hats declares — live pyproject first, METADATA second.

    HATS-1368: on an editable install METADATA is a snapshot taken at install
    time, and its drift from the code actually on ``.pth`` is one-sided in BOTH
    directions — it can under-declare (a workspace member added since, invisible
    to the gate) or over-declare (a member deleted since, healed forever as a
    no-op). The checkout's own pyproject.toml cannot drift from the code it sits
    next to. A wheel install has no such gap, so it keeps reading METADATA.
    """
    src = _editable_source_dir()
    if src is not None:
        live = _live_pyproject_deps(src)
        if live is not None:
            return live
    try:
        return list(importlib.metadata.requires("ai-hats") or [])
    except importlib.metadata.PackageNotFoundError:
        return []


def expected_runtime_deps() -> list[tuple[str, str]]:
    """Return ``[(dist_name, import_name), ...]`` for runtime deps of ai-hats.

    Source of truth is :func:`_declared_requirements` — auto-syncs with
    ``pyproject.toml`` so any new dep is protected without touching this module.
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for req in _declared_requirements():
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


def attempt_self_heal(missing: list[str], *, repair_editable: bool = False) -> bool:
    """Run the repair install for dependency or editable-metadata drift."""

    if not missing and not repair_editable:
        return True
    try:
        result = subprocess.run(_repair_argv(missing), check=False)
    except OSError:
        return False
    if result.returncode != 0:
        return False
    _refresh_import_paths()
    return True


def _refresh_import_paths() -> None:
    """Make what uv just installed visible to THIS interpreter.

    HATS-1368: an editable install lands as a ``.pth`` file, and ``.pth`` files
    are processed only at interpreter startup — without re-running the site hook
    the healed module stays unimportable here, and the HATS-1359 recheck reads a
    successful heal as a no-op.
    """
    import site
    import sysconfig

    try:
        site.addsitedir(sysconfig.get_paths()["purelib"])
    except Exception:  # silent-ok: a refresh that fails must not abort the heal  # noqa: S110
        pass
    importlib.invalidate_caches()


def _repair_argv(missing: list[str]) -> list[str]:
    """The one repair invocation — what bootstrap runs and what it tells users to run.

    HATS-1367: installing an editable install's deps BY NAME is the no-op uv
    audits as already-satisfied; only re-pointing the checkout rewrites the
    metadata that went stale. Two renderings of one command, so the printed
    rescue can never drift from the attempted heal.
    """
    src = _editable_source_dir()
    tail = ["-e", src] if src is not None else list(missing)
    return ["uv", "pip", "install", "--python", sys.executable, *tail]


def _rescue_command(missing: list[str]) -> str:
    argv = _repair_argv(missing)
    head, tail = argv[:5], argv[5:]
    return " ".join([*head, *(a if a == "-e" else f"'{a}'" for a in tail)])


def repair_command() -> str:
    """The command to hand a user whose install is broken in an unknown way.

    HATS-1368: ``self update`` is a dead end when the import that broke is the
    CLI that would run it — an editable install with stale metadata answers the
    advice with the very error that produced it. Re-pointing the checkout is the
    repair that runs from outside the broken tree.
    """
    src = _editable_source_dir()
    if src is None:
        return "python -m ai_hats self update (or 'ai-hats self update')"
    return _rescue_command([])


def bootstrap_or_die() -> None:
    """Repair runtime or editable-metadata drift before importing the CLI.

    Called as the very first action in :func:`ai_hats.__main__.main`. Side effects:
    prints to stderr, runs uv in a subprocess, and on success replaces the
    current process via :func:`os.execv` so freshly-installed modules become
    importable for the actual command the user invoked.
    """
    missing = find_missing_runtime_deps()
    metadata_drift = find_editable_entry_point_drift()
    if not missing and not metadata_drift:
        return

    problems = list(metadata_drift)
    if missing:
        problems.insert(0, f"missing runtime deps {missing}")
    sys.stderr.write(
        f"ai-hats: {'; '.join(problems)}; healing via uv…\n"
        f"  manual command if this fails: {_rescue_command(missing)}\n"
    )
    sys.stderr.flush()

    if not attempt_self_heal(missing, repair_editable=bool(metadata_drift)):
        sys.stderr.write("ai-hats: self-heal failed. Run the manual command above, then retry.\n")
        sys.exit(1)

    # HATS-1359: uv can exit 0 as a no-op (stale dist-info, import still
    # broken) — recheck before re-exec'ing forever into the same state.
    still_missing = find_missing_runtime_deps()
    still_drift = find_editable_entry_point_drift()
    if still_missing or still_drift:
        remaining = list(still_drift)
        if still_missing:
            remaining.insert(0, f"missing runtime deps {still_missing}")
        sys.stderr.write(
            f"ai-hats: uv reported success but {'; '.join(remaining)} remains "
            "(stale or orphaned install metadata?).\n"
            f"  manual command: {_rescue_command(still_missing)}\n"
        )
        sys.exit(1)

    # Re-exec a fresh interpreter so that freshly-installed modules can be
    # imported. argv[0] becomes the interpreter; the rest is whatever the
    # user originally invoked.
    os.execv(sys.executable, [sys.executable, "-m", "ai_hats", *sys.argv[1:]])


def _is_first_party(ep: importlib.metadata.EntryPoint) -> bool:
    """True when ai-hats itself ships this entry point.

    Duplicates provider_entry_points._is_first_party_entry_point (HATS-1121).
    _bootstrap is contractually stdlib-only and must not depend on project modules
    it may be verifying.
    """
    dist = getattr(ep, "dist", None)
    if dist is None:
        return False
    name = getattr(dist, "name", None)
    if not name and hasattr(dist, "metadata"):
        name = dist.metadata.get("Name")
    if not name:
        return False
    return _normalise(name) == "ai-hats"


def _first_party_entry_point_failures(group: str) -> list[str]:
    """Load every ai-hats-owned entry point in ``group``; report failures.

    Out-of-tree provider plugins are skipped on purpose: a third-party plugin
    must not fail the install verify, matching the runtime plugin policy.
    """
    try:
        eps = list(importlib.metadata.entry_points(group=group))
    except Exception as exc:  # noqa: BLE001 - verify must report, never crash
        return [f"entry_points({group}): {exc.__class__.__name__}: {exc}"]

    failures: list[str] = []
    for ep in eps:
        if not _is_first_party(ep):
            continue
        try:
            # ep.load() resolves the ATTRIBUTE; find_spec would only prove the
            # module exists and would pass on a retired provider (HATS-1116).
            ep.load()
        except Exception as exc:  # noqa: BLE001 - collect, don't abort the sweep
            failures.append(
                f"{group} entry point {ep.name!r} ({ep.value}): {exc.__class__.__name__}: {exc}"
            )
    return failures


def _check_pycache_coherence() -> list[str]:
    """Check __pycache__ bytecode headers against source .py files in ai_hats package.

    Detects stale .pyc files whose recorded source mtime or size no longer matches
    the on-disk .py file (PEP 552 mtime-based bytecode validation).
    """
    failures: list[str] = []
    try:
        spec = importlib.util.find_spec("ai_hats")
    except Exception:  # silent-ok: module-presence probe; absence is the answer, not an error
        return failures

    if not spec or not spec.submodule_search_locations:
        return failures

    import struct

    for search_dir in spec.submodule_search_locations:
        pycache_dir = os.path.join(search_dir, "__pycache__")
        if not os.path.isdir(pycache_dir):
            continue

        try:
            entries = os.listdir(pycache_dir)
        except OSError:
            continue

        for pyc_name in entries:
            if not pyc_name.endswith(".pyc"):
                continue
            pyc_path = os.path.join(pycache_dir, pyc_name)
            try:
                with open(pyc_path, "rb") as f:
                    header = f.read(16)
            except OSError:
                continue

            if len(header) < 16:
                continue

            magic, flags, recorded_mtime, recorded_size = struct.unpack("<IIII", header)
            if flags != 0:
                continue

            stem = pyc_name.split(".", 1)[0]
            source_path = os.path.join(search_dir, f"{stem}.py")
            if not os.path.isfile(source_path):
                continue

            try:
                st = os.stat(source_path)
            except OSError:
                continue

            py_mtime = int(st.st_mtime) & 0xFFFFFFFF
            py_size = st.st_size & 0xFFFFFFFF

            if recorded_mtime != py_mtime or recorded_size != py_size:
                try:
                    os.unlink(pyc_path)  # safe-delete: ok pycache-autoheal
                except OSError:
                    failures.append(
                        f"stale __pycache__: {pyc_path} recorded mtime/size ({recorded_mtime}/{recorded_size}) "
                        f"does not match {source_path} ({py_mtime}/{py_size})"
                    )

    return failures


def find_integrity_failures() -> list[str]:
    """Report why the installed ai-hats tree is unusable, one line per failure.

    HATS-1116: :func:`find_missing_runtime_deps` only sees third-party
    distributions, so it cannot notice an ai_hats tree whose own modules
    disagree with each other — the exact state that shipped a green install.
    """
    importlib.invalidate_caches()  # deps may have just been healed in-process
    failures: list[str] = []
    for mod in _INTEGRITY_MODULES:
        try:
            importlib.import_module(mod)
        except Exception as exc:  # noqa: BLE001 - report the reason, don't raise
            failures.append(f"import {mod}: {exc.__class__.__name__}: {exc}")
    failures.extend(_first_party_entry_point_failures("ai_hats.providers"))
    failures.extend(_check_pycache_coherence())
    return failures


def verify_after_install() -> int:
    """Stage-2 verify after an ai-hats install. No re-exec; returns exit code.

    Designed to be run via ``python -m ai_hats._bootstrap verify`` in a
    fresh subprocess from :func:`ai_hats.cli.maintenance.update` and from
    ``self init``'s embedded update. Because it's a fresh process, it reads
    the just-installed on-disk code.
    """
    missing = find_missing_runtime_deps()
    if missing:
        sys.stderr.write(f"ai-hats: post-install verify found missing deps {missing}; healing…\n")
        if attempt_self_heal(missing):
            # Re-check — uv can succeed but install nothing useful in pathological
            # cases (e.g. wheel for wrong platform). Trust but verify.
            still_missing = find_missing_runtime_deps()
            if still_missing:
                sys.stderr.write(f"ai-hats: deps still missing after uv install: {still_missing}\n")
                sys.stderr.write(f"  manual command: {_rescue_command(missing)}\n")
                return 1
        else:
            sys.stderr.write(f"  manual command: {_rescue_command(missing)}\n")
            return 1

    failures = find_integrity_failures()
    for group in _FIRST_PARTY_ENTRY_POINT_GROUPS:
        failures.extend(_first_party_entry_point_failures(group))
    if failures:
        sys.stderr.write("ai-hats: post-install verify found a broken install:\n")
        for line in failures:
            sys.stderr.write(f"  - {line}\n")
        return 1
    return 0


def _main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "verify":
        return verify_after_install()
    sys.stderr.write("usage: python -m ai_hats._bootstrap verify\n")
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
