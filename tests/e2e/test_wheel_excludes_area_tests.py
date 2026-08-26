"""e2e (HATS-1783)

flow:   a user installs the released ai-hats wheel and gets every module of the
        `pipeline` area — but none of the area's own test suite
cmds:
    uv build --wheel --out-dir <tmp>/wheels <per-worker clone of the repo>
expect: `ai_hats/pipeline/loader.py` and every other module of the area are in
        the wheel; nothing under `ai_hats/pipeline/tests/` is, and no `tests/`
        tree ships anywhere inside the package
why:    ADR-0026 D5 keeps an area's tests inside the area folder, and
        `[tool.hatch.build.targets.wheel] packages = ["src/ai_hats"]` ships that
        folder whole — the D11 exclude is the only thing between a test suite and
        every user's site-packages. Deleting that one line is invisible to the
        rest of the suite, so the built artefact is what gets asserted here, not
        the config that produced it
"""  # comment-length: allow — the e2e catalog header format

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

import pytest

from _helpers.env import clean_env  # noqa: E402
from _helpers.repo_src import build_src  # noqa: E402
from _helpers.venv import network_available, venv_unavailable  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: Areas laid out per ADR-0026 D5 — one entry each, as `testpaths` lists them.
#: `pipeline` is the pilot (HATS-1783); `surfaces` is the fold (HATS-1826), whose
#: five nested test trees the catch-all at the bottom already covers.
AREAS = ("pipeline", "surfaces")

BUILD_TIMEOUT_S = 180


def _area_modules(area: str) -> set[str]:
    """Every module of the area, under the path the wheel must carry it at."""
    root = REPO_ROOT / "src" / "ai_hats" / area
    return {
        f"ai_hats/{area}/{path.relative_to(root).as_posix()}"
        for path in root.rglob("*.py")
        if "tests" not in path.relative_to(root).parts and "__pycache__" not in path.parts
    }


def _build_wheel(tmp_path: Path) -> set[str]:
    """Build the integrator wheel and return the names it carries."""
    if not network_available():
        venv_unavailable("uv not on PATH — cannot build the integrator wheel")
    # A per-worker clone, so no build runs in the developer's checkout
    # (HATS-1560) — and so this reads COMMITTED content (HATS-1651).
    src = build_src(REPO_ROOT)
    wheeldir = tmp_path / "wheels"
    built = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(wheeldir), str(src)],
        cwd=str(tmp_path),
        env=clean_env(),
        capture_output=True,
        text=True,
        timeout=BUILD_TIMEOUT_S,
    )
    assert built.returncode == 0, f"uv build failed:\n{built.stdout}\n{built.stderr}"
    wheels = sorted(wheeldir.glob("ai_hats-*.whl"))
    assert wheels, f"no ai-hats wheel built under {wheeldir}"
    with zipfile.ZipFile(wheels[0]) as zf:
        return set(zf.namelist())


@pytest.mark.integration
def test_wheel_ships_the_area_without_its_tests(tmp_path: Path) -> None:
    names = _build_wheel(tmp_path)

    assert "ai_hats/pipeline/loader.py" in names, (
        "the wheel lost ai_hats/pipeline/loader.py — the D11 exclude is dropping "
        "the area itself, not just its tests"
    )
    for area in AREAS:
        missing = sorted(_area_modules(area) - names)
        assert not missing, f"the {area} area is incomplete in the wheel: {missing}"

        shipped = sorted(n for n in names if n.startswith(f"ai_hats/{area}/tests/"))
        assert not shipped, (
            f"the wheel ships the {area} area's tests: {shipped} — restore "
            "`exclude` under [tool.hatch.build.targets.wheel] (ADR-0026 D11)"
        )

    stray = sorted(n for n in names if n.startswith("ai_hats/") and "/tests/" in n)
    assert not stray, f"a test tree rode into the wheel: {stray} (ADR-0026 D11)"
