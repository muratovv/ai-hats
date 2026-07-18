"""Stale editable dev-env detection (HATS-1013).

uv editable installs freeze dist-info metadata at sync time, so after any
version bump ``importlib.metadata`` / ``--version`` lie until ``uv sync``.
Verdict is delegated to ``uv sync --check`` (documented exit code); output
lines are best-effort message enrichment only. Design: plan.md.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ENV_DRIFT_FIX = "uv sync --inexact --all-packages"
_CHECK_TIMEOUT_SECONDS = 15

# " - name==ver (from file://...)" / " + name @ file://..." — only file:// lines
# are workspace members; dep-only changes fall through to the generic message.
_MEMBER_LINE = re.compile(r"^\s*([-+])\s+(\S+?)(?:==(\S+))?\s.*file://", re.MULTILINE)


def _member_changes(output: str) -> list[str]:
    old: dict[str, str | None] = {}
    new: dict[str, str | None] = {}
    for sign, name, version in _MEMBER_LINE.findall(output):
        (old if sign == "-" else new)[name] = version or None
    parts = []
    for name in sorted(old):
        before, after = old[name], new.get(name)
        parts.append(f"{name} {before} -> {after}" if before and after else name)
    return parts


def stale_dev_env_warnings(
    *,
    repo_root: Path | None = None,
    venv_prefix: Path | None = None,
    runner=subprocess.run,
    which=shutil.which,
) -> list[str]:
    """One aggregated warning when the editable dev env is out of sync, else [].

    Gated to the dev checkout: ai-hats installed editable AND this venv being
    ``<repo_root>/.venv`` (uv sync targets the project env, nothing else).
    Identity = resolved ``sys.prefix`` — venv interpreters are symlinks OUT of
    the venv, so comparing executables false-negatives. Fail-open on every
    operational error — a lint must never block launch.
    """
    if repo_root is None:
        from .paths import editable_install_root

        repo_root = editable_install_root("ai-hats")
    if repo_root is None:
        return []
    prefix = Path(venv_prefix or sys.prefix)
    if prefix.resolve() != (repo_root / ".venv").resolve():
        return []
    if which("uv") is None:
        return []
    try:
        proc = runner(
            ["uv", "sync", "--check", "--inexact", "--all-packages", "--project", str(repo_root)],
            capture_output=True,
            text=True,
            timeout=_CHECK_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 1:  # 0 = in sync; >=2 = uv error -> fail open
        return []
    changes = _member_changes(f"{proc.stdout}\n{proc.stderr}")
    detail = f": stale {', '.join(changes)}" if changes else ""
    return [f"dev env outdated{detail} — run:\n    {ENV_DRIFT_FIX}"]


__all__ = ["ENV_DRIFT_FIX", "stale_dev_env_warnings"]
