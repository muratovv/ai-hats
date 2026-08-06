"""Build a real editable ai-hats venv and put it in the stale-METADATA state.

Shared by the two HATS-1368/HATS-1367 bootstrap e2e tests: both need the same
class-B venv (an editable install whose METADATA predates the workspace split)
and differ only in what they assert about the gate's response.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path


def build_editable_venv(
    work_dir: Path, repo_root: Path, *, venv_name: str = "venv"
) -> tuple[Path, Path]:
    """``uv venv`` + ``uv pip install -e <repo_root>``. Returns ``(venv_python, checkout)``.

    Installs the working tree, NOT a clone: a clone carries committed state only,
    so an uncommitted change to the gate would not reach the subprocess and the
    fail-under-revert check would pass against code that no longer exists.
    Nothing here writes to ``repo_root`` — the breakage is applied to the venv.
    """
    checkout = repo_root
    venv = work_dir / venv_name
    subprocess.run(
        ["uv", "venv", str(venv), "--python", "3.13"],
        check=True,
        capture_output=True,
        timeout=300,
    )
    venv_python = venv / "bin" / "python"
    subprocess.run(
        ["uv", "pip", "install", "--python", str(venv_python), "-e", str(checkout)],
        check=True,
        capture_output=True,
        timeout=600,
    )
    return venv_python, checkout


def site_packages(venv_python: Path) -> Path:
    cands = sorted((venv_python.parent.parent / "lib").glob("python*/site-packages"))
    assert cands, f"no site-packages under {venv_python}"
    return cands[0]


def make_metadata_predate_workspace_split(venv_python: Path) -> list[str]:
    """Reproduce the 0.8.x-epoch state: METADATA declares no first-party deps.

    Strips every ``Requires-Dist: ai-hats-*`` line and removes the members those
    lines used to protect — the exact shape the 2026-07-30 sweep found in 8
    consumer venvs, where the live code still imports what METADATA forgot.
    Returns the module names made unimportable.
    """
    sp = site_packages(venv_python)
    metadata = next(iter(sp.glob("ai_hats-*.dist-info/METADATA")))
    kept = [
        line
        for line in metadata.read_text().splitlines(keepends=True)
        if not re.match(r"^Requires-Dist: ai-hats-", line)
    ]
    metadata.write_text("".join(kept))

    modules = []
    for pth in sp.glob("_editable_impl_ai_hats_*.pth"):
        module = pth.stem.replace("_editable_impl_", "")
        pth.unlink()
        for dist_info in sp.glob(f"{module}-*.dist-info"):
            shutil.rmtree(dist_info)
        modules.append(module)
    assert modules, "no first-party editable .pth found — setup assumption broke"
    return modules


def imports(venv_python: Path, module: str, env=None) -> bool:
    return (
        subprocess.run(
            [str(venv_python), "-c", f"import {module}"],
            capture_output=True,
            env=env,
            timeout=60,
        ).returncode
        == 0
    )


def module_file(venv_python: Path, module: str) -> str:
    """Where ``module`` resolves from, or ``""``. Distinguishes editable from wheel.

    The workspace members are published, so installing them BY NAME succeeds —
    with PyPI wheels that silently displace the developer's editable checkout
    and can be older than it. "Importable" alone cannot tell the two apart.
    """
    result = subprocess.run(
        [str(venv_python), "-c", f"import {module}; print({module}.__file__)"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.stdout.strip() if result.returncode == 0 else ""
