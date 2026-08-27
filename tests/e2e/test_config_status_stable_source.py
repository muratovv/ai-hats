"""e2e (HATS-779)

flow:   a user running a release package installed from PyPI inspects source
        provenance in configuration status
cmds:
    ai-hats config status
expect: the Source line in status output displays "stable @ PyPI" instead of the
        "(unknown — direct_url.json missing)" fallback
why:    standard PyPI package installations omit direct_url.json metadata, requiring
        package distribution fallback to identify stable releases
"""

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

import pytest

from _helpers.env import clean_env  # noqa: E402
from _helpers.repo_src import build_src  # noqa: E402
from _helpers.venv import network_available, venv_unavailable  # noqa: E402
from _helpers.workspace import build_workspace_member_wheels  # noqa: E402
from ai_hats.paths import ENV_AI_HATS_VENV

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# HATS-678/771: real uv build + install at call time → capped via conftest.INSTALL_HEAVY_GROUPS.
pytestmark = pytest.mark.install_heavy


def _run(cmd, *, cwd, env, timeout, expect_exit=0):
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if expect_exit is not None and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _wheel_version(wheel: Path) -> str:
    """Canonical version from the wheel's METADATA (NOT the filename — the wheel
    filename escapes a PEP 440 local segment ``+g<sha>`` to ``_g<sha>``, which is
    not a valid ``==`` specifier)."""
    with zipfile.ZipFile(wheel) as zf:
        meta_name = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
        meta = zf.read(meta_name).decode()
    for line in meta.splitlines():
        if line.startswith("Version:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"no Version in {wheel} METADATA")


@pytest.mark.integration
def test_e2e_config_status_stable_source(tmp_path):
    if not network_available():
        venv_unavailable("uv not on PATH — cannot build/install the stable wheel")

    env = clean_env()  # HATS-685: drop inherited PYTHONPATH/redirect vars
    env.pop(ENV_AI_HATS_VENV, None)  # never leak from outer test runs

    # 1. Build the ai-hats wheel from a per-worker private clone (no in-tree race).
    src = build_src(REPO_ROOT)
    wheeldir = tmp_path / "wheels"
    _run(
        ["uv", "build", "--wheel", "--out-dir", str(wheeldir), str(src)],
        cwd=tmp_path,
        env=env,
        timeout=180,
    )
    wheels = sorted(wheeldir.glob("ai_hats-*.whl"))
    assert wheels, f"no ai-hats wheel built under {wheeldir}"
    version = _wheel_version(wheels[0])
    # HATS-898: ai-hats requires ai-hats-core/ai-hats-wt (unpublished) — build the
    # workspace member wheels into the same find-links dir so the install resolves.
    build_workspace_member_wheels(src, wheeldir, env)

    # 2. Fresh venv + install ai-hats BY NAME from the local find-links dir. Pinning
    #    to the exact locally-built (dev+local) version guarantees the local wheel
    #    wins over any released PyPI version — so this exercises THIS code, and the
    #    name-based requirement means uv writes no direct_url.json (the PyPI case).
    venv = tmp_path / "venv"
    _run(["uv", "venv", "--python", "3.13", str(venv)], cwd=tmp_path, env=env, timeout=120)
    _run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(venv / "bin" / "python"),
            "--find-links",
            str(wheeldir),
            f"ai-hats=={version}",
        ],
        cwd=tmp_path,
        env=env,
        timeout=300,
    )

    # HATS-790: a by-name install no longer materialises a bin/ai-hats console
    # script — invoke via the venv interpreter and assert the proxy is ABSENT.
    py = venv / "bin" / "python"
    assert py.is_file(), "venv python missing after by-name install"
    assert not (venv / "bin" / "ai-hats").exists(), (
        "bin/ai-hats console script must NOT exist after by-name install (HATS-790)"
    )

    # SANITY: confirm the by-name install reproduced the PyPI case (no direct_url.json).
    dist_info = sorted((venv / "lib").glob("python*/site-packages/ai_hats-*.dist-info"))
    assert dist_info, "ai-hats dist-info missing after install"
    assert not (dist_info[0] / "direct_url.json").exists(), (
        "by-name install unexpectedly wrote direct_url.json — test premise broken "
        "(would mean uv resolved a direct-URL candidate, not an index-by-name one)"
    )

    # 3. config status in a role-less project — install Health fields render
    #    regardless of role (HATS-497), so the Source line is present.
    project = tmp_path / "project"
    project.mkdir()
    res = _run([str(py), "-m", "ai_hats", "config", "status"], cwd=project, env=env, timeout=60)
    out = res.stdout + res.stderr
    assert "Source:" in out, f"no Source line in config status:\n{out}"
    assert "stable @ PyPI" in out, (
        f"stable/released-wheel install Source not 'stable @ PyPI':\n{out}"
    )
    assert "(unknown — direct_url.json missing)" not in out, (
        f"stable install still mislabelled as unknown (HATS-779 reverted?):\n{out}"
    )
