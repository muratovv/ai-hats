"""Remove distributions ai-hats has retired but the venv still carries (HATS-1280).

``self update`` installs, it does not synchronize: a dependency a new version
DROPPED stays installed, console script and all. For ``ai-hats-tracker`` that
left a working legacy backlog CLI over the same store — the surface epic
HATS-1159 exists to remove, surviving the upgrade under another name.

Runs from :mod:`ai_hats._bump_internal`, the hook ``self update`` invokes in a
fresh interpreter against the freshly-installed tree. That placement is the
whole mechanism and is load-bearing twice over:

* **it runs the new code during the upgrade to it** — the OLD version issues the
  call, so a prune shipped in a release acts on the upgrade *to* that release;
  a hook the old version does not already invoke would never fire.
* **it cannot break an upgrade** — the bump runs after the ``.complete`` sentinel
  and the ``current`` flip, and every released caller treats a non-zero bump as
  a warning.

Two targets, deliberately asymmetric:

* the **running interpreter** holds the new ai-hats, which no longer declares
  the retired dep — safe to uninstall outright;
* the legacy ``<ai_hats_dir>/.venv`` holds the **old** ai-hats, whose metadata
  still declares it. Uninstalling there would make that interpreter's
  :func:`ai_hats._bootstrap.bootstrap_or_die` see a missing declared dependency
  and try to heal it — observed spinning forever. So only the console script
  goes: the CLI becomes unreachable by name, which is the goal, and the venv is
  discarded wholesale by ``reclaim_legacy_venv`` on the next update anyway.
"""  # comment-length: allow

from __future__ import annotations

import importlib.metadata
import os
import shutil
import subprocess
import sys
from pathlib import Path

from ._bootstrap import _normalise, expected_runtime_deps

#: Retired distribution → the console scripts it installs. Explicit, not derived:
#: a dependency-closure diff would need graph resolution the installer does not
#: expose, and the retired set is finite and known (HATS-1280 ruling).
RETIRED_DISTRIBUTIONS: dict[str, tuple[str, ...]] = {
    "ai-hats-tracker": ("ai-hats-tracker",),
    # HATS-1826 folded the surface distributions into `ai-hats`. Only the two
    # that reached PyPI are listed: `ai-hats-codex` and `ai-hats-opencode` never
    # published (the index answers 404), so no venv can be carrying them.
    "ai-hats-agy": ("ai-hats-hook-dispatcher",),
    "ai-hats-cline": (),
}

#: Set by ``tests/conftest.py``. Without it a unit run that reaches this module
#: would uninstall from the developer's own venv.
ENV_SKIP_PRUNE = "AI_HATS_SKIP_RETIRED_PRUNE"

_UNINSTALL_TIMEOUT_S = 30


def _still_declared() -> set[str]:
    """Normalised dists the installed ai-hats still requires — never prune these.

    Makes "prune something still needed" impossible rather than unlikely: under a
    cherry-pick, a ``--revision`` install, or a future typo in the retired set,
    the name simply resolves as live and is skipped.

    Probing our own dist first is what makes the guard real. ``expected_runtime_deps``
    swallows a missing ai-hats and answers ``[]`` — indistinguishable from "declares
    nothing", so an interpreter that cannot read its own metadata (a PYTHONPATH source
    run, a clobbered dist-info) would otherwise read as "prune everything" (HATS-1280).
    """
    retired = {_normalise(name) for name in RETIRED_DISTRIBUTIONS}
    try:
        importlib.metadata.distribution("ai-hats")
        declared = {_normalise(dist) for dist, _ in expected_runtime_deps()}
    except Exception:  # silent-ok: a guard that crashes must not prune
        return retired
    return declared or retired


def _warn(message: str) -> None:
    """One line to stderr — a silent failure is indistinguishable from "nothing
    to do", and the prune gets one shot per user per upgrade."""
    try:
        print(f"ai-hats: {message}", file=sys.stderr)
    except Exception:  # silent-ok: reporting must not be what raises  # noqa: S110
        pass


def _is_installed(name: str) -> bool:
    try:
        importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        return False
    except Exception:  # silent-ok: unreadable metadata is not our business
        return False
    return True


def _uninstall(name: str, python_exe: str) -> bool:
    """``uv pip uninstall`` one distribution. False on any failure — never raises.

    ``start_new_session`` keeps a terminal SIGINT off uv mid-write, and the
    timeout matters more than the exception handling: a hang is not catchable by
    ``except`` and uv locks the target environment, so a concurrent install would
    otherwise wedge the upgrade behind a captured-output spinner.
    """
    uv = shutil.which("uv")
    if uv is None:
        _warn(f"cannot remove retired {name}: uv not on PATH")
        return False
    try:
        proc = subprocess.run(
            [uv, "pip", "uninstall", "--python", python_exe, name],
            capture_output=True,
            text=True,
            timeout=_UNINSTALL_TIMEOUT_S,
            start_new_session=True,
        )
    except BaseException as exc:  # noqa: BLE001 - incl. TimeoutExpired and KeyboardInterrupt
        _warn(f"cannot remove retired {name}: {type(exc).__name__}")
        return False
    if proc.returncode != 0:
        _warn(f"cannot remove retired {name}: uv exited {proc.returncode}")
        return False
    return True


def prune_running_interpreter() -> list[str]:
    """Uninstall retired dists from the interpreter this code runs in."""
    declared = _still_declared()
    removed: list[str] = []
    for name in RETIRED_DISTRIBUTIONS:
        if _normalise(name) in declared or not _is_installed(name):
            continue
        if _uninstall(name, sys.executable):
            removed.append(name)
    return removed


def strip_retired_scripts(venv_dir: Path, project_dir: Path | None = None) -> list[str]:
    """Remove retired console scripts from another venv, leaving its dists alone.

    For the legacy ``.venv`` only — see the module docstring for why the
    distribution itself must stay. Goes through ``safe_delete`` so a wrong prune
    is recoverable from the trash session rather than gone (HATS-470).
    """
    from ai_hats_core.safe_delete import discard

    declared = _still_declared()
    removed: list[str] = []
    for name, scripts in RETIRED_DISTRIBUTIONS.items():
        if _normalise(name) in declared:
            continue
        for script in scripts:
            for path in (venv_dir / "bin" / script, venv_dir / "Scripts" / f"{script}.exe"):
                try:
                    if not (path.is_file() or path.is_symlink()):
                        continue
                    discard(
                        path, reason="retired distribution (HATS-1280)", project_dir=project_dir
                    )
                except (OSError, ValueError) as exc:
                    _warn(f"cannot remove retired {path}: {type(exc).__name__}")
                    continue
                # State, not the call's return: discard deletes before it records,
                # so a failed manifest write must still count as removed.
                if not (path.is_file() or path.is_symlink()):
                    removed.append(str(path))
    return removed


def prune_retired(project_dir: Path) -> list[str]:
    """Both targets, in one call. Reports what it removed; never raises.

    The caller is a bump whose exit code must not depend on this — so every
    failure mode ends as "nothing removed", not as an exception.
    """
    if os.environ.get(ENV_SKIP_PRUNE):
        return []
    removed: list[str] = []
    try:
        from .paths import editable_install_root

        # A dev checkout resolves `packages/*` as workspace members, so on a ref
        # predating the retirement `uv sync` reinstalls what we removed, forever.
        if editable_install_root("ai-hats") is not None:
            return []
        removed += prune_running_interpreter()
        from .paths import ai_hats_dir

        legacy = ai_hats_dir(project_dir) / ".venv"
        if legacy.is_dir() and legacy.resolve() != Path(sys.prefix).resolve():
            removed += strip_retired_scripts(legacy, project_dir)
    except BaseException as exc:  # noqa: BLE001 - a prune must never fail an upgrade
        _warn(f"retired-distribution prune aborted: {type(exc).__name__}")
        return removed
    return removed
