"""e2e (HATS-1877)

flow:   a maintainer publishes a package whose sdist silently dropped a file
cmds:
    bash scripts/ci-local.sh wheel-contents
    bash scripts/ci-local.sh --stages merge-gate
expect: the stage is green on this tree; with the library's sdist
        `force-include` removed it names the six `hooks/consent_gate` files
        that no published wheel ever carried; and `merge-gate` names the stage
why:    `uv build <pkg>` builds the sdist and then the wheel FROM it, which is
        what every release workflow here does. `uv build --wheel` builds from
        the tree instead and carries those six files ONLY under the symlink
        that reaches them — on a healthy tree as much as a broken one. So the
        chain is not an optimisation to undo: a `--wheel` build makes the check
        red on a tree that is fine, which the green case below pins
"""  # comment-length: allow — the e2e catalog header format

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from _helpers.repo_src import build_src  # noqa: E402
from _helpers.venv import network_available, venv_unavailable  # noqa: E402

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]

STAGE_TIMEOUT_S = 600

#: The one line whose removal reproduces the v0.15.0 release blocker.
FORCE_INCLUDE = """[tool.hatch.build.targets.sdist.force-include]
"src/ai_hats_library/hooks/consent_gate" = "src/ai_hats_library/hooks/consent_gate"
"""

#: What the sdist dropped, and what `ai_hats.consent_wrapper` imports.
DROPPED = (
    "ai_hats_library/hooks/consent_gate/__init__.py",
    "ai_hats_library/hooks/consent_gate/check.py",
    "ai_hats_library/hooks/consent_gate/cli.py",
    "ai_hats_library/hooks/consent_gate/host.py",
    "ai_hats_library/hooks/consent_gate/issue.py",
    "ai_hats_library/hooks/consent_gate/operations.py",
)


def _require_uv() -> None:
    if not network_available():
        venv_unavailable("uv not on PATH — nothing can be built")


def test_the_stage_is_green_on_this_tree():
    """Also the guard on the build itself: a `--wheel` build never carries the
    real `hooks/consent_gate` path, so switching the checker to that flag turns
    this green case red."""
    _require_uv()
    run = subprocess.run(
        ["bash", "scripts/ci-local.sh", "wheel-contents"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=STAGE_TIMEOUT_S,
    )
    combined = run.stdout + run.stderr
    assert run.returncode == 0, combined
    assert "[ci-local] wheel-contents" in combined, combined
    assert "ai-hats-library" in combined, combined


def test_the_release_blocker_is_named_when_the_fix_is_reverted():
    """The RED baseline HATS-1877 exists to hold, and an exact one: the commit
    that fixed it is `e3c6643a`, so this is a revert and not a construction."""
    _require_uv()
    clone = build_src(REPO_ROOT)
    # The clone materialises COMMITTED content, so carry in the checker under
    # test: otherwise this measures whatever the last commit happened to hold.
    checker = clone / "scripts" / "check_wheel_contents.py"
    shutil.copy2(REPO_ROOT / "scripts" / "check_wheel_contents.py", checker)
    pyproject = clone / "packages" / "ai-hats-library" / "pyproject.toml"
    original = pyproject.read_text(encoding="utf-8")
    if FORCE_INCLUDE not in original:
        pytest.skip("the clone predates the sdist force-include")
    pyproject.write_text(original.replace(FORCE_INCLUDE, "", 1), encoding="utf-8")
    try:
        run = subprocess.run(
            [
                sys.executable,
                str(checker),
                "packages/ai-hats-library",
            ],
            cwd=str(clone),
            capture_output=True,
            text=True,
            timeout=STAGE_TIMEOUT_S,
        )
    finally:
        pyproject.write_text(original, encoding="utf-8")
    combined = run.stdout + run.stderr

    assert run.returncode == 1, combined
    for path in DROPPED:
        assert path in combined, combined


def test_the_merge_gate_names_the_stage():
    """The gate runs what `--stages` names, so dropping it here disarms it."""
    listed = subprocess.run(
        ["bash", "scripts/ci-local.sh", "--stages", "merge-gate"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert listed.returncode == 0, listed.stdout + listed.stderr
    assert "wheel-contents" in listed.stdout.split(), listed.stdout
