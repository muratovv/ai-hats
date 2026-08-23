"""e2e (HATS-1783)

flow:   a user installs the released ai-hats wheel and runs a pipeline; every
        built-in step it names has to resolve, and it resolves only through the
        installed distribution's entry-point metadata
cmds:
    uv build --wheel --out-dir <tmp>/wheels <per-worker clone of the repo>
    uv venv <tmp>/venv && uv pip install --no-deps <wheel> pyyaml
    <tmp>/venv/bin/python -c "load_pipeline(<one-step yaml>)"
expect: the installed dist advertises all 23 built-in step ids under
        `ai_hats.steps`; loading a YAML that names `pre_log` builds the step,
        imports `ai_hats.pipeline.steps.log` and NO other step module, and an
        unknown id fails loudly naming what is known
why:    the step ids left the source tree for `[project.entry-points]` in
        pyproject.toml, and nothing in the source tree can tell whether that
        block reached the built distribution's `entry_points.txt`. A unit test
        of the registry passes against the developer's editable install no
        matter what the wheel carries; drop the block and every pipeline stops
        resolving, in an artefact no in-tree test opens. This runs the resolver
        against a real install, from a venv the checkout is not on the path of
"""  # comment-length: allow — the e2e catalog header format

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from _helpers.env import clean_env  # noqa: E402
from _helpers.repo_src import build_src  # noqa: E402
from _helpers.venv import network_available, venv_unavailable  # noqa: E402

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]

BUILD_TIMEOUT_S = 180
PROBE_TIMEOUT_S = 120

#: The id under test: `pre_log` reaches the lightest step module there is
#: (`steps.log` imports only `step` and `trace`), so what the probe observes in
#: `sys.modules` is the resolver's doing and not some neighbour's import.
STEP_ID = "pre_log"
STEP_MODULE = "ai_hats.pipeline.steps.log"

#: Runs inside the installed venv and reports; every assertion is made by the
#: test, so a probe failure is legible rather than a bare non-zero exit.
PROBE = """
import importlib.metadata, json, sys, tempfile
from pathlib import Path

advertised = sorted(ep.name for ep in importlib.metadata.entry_points(group="ai_hats.steps"))

from ai_hats.pipeline import registry
from ai_hats.pipeline.loader import load_pipeline

imported_at_import_time = sorted(m for m in sys.modules if m.startswith("ai_hats.pipeline.steps"))

yaml_path = Path(tempfile.mkdtemp()) / "probe.yaml"
yaml_path.write_text("name: probe\\nsteps:\\n  - id: %s\\n    params: {keys: [a]}\\n" % sys.argv[1])
pipeline = load_pipeline(yaml_path)

unknown = ""
try:
    registry.get("no_such_step")
except registry.StepRegistryError as exc:
    unknown = str(exc)

print(json.dumps({
    "advertised": advertised,
    "imported_at_import_time": imported_at_import_time,
    "steps": [s.io.name for s in pipeline.steps],
    "imported_after_load": sorted(m for m in sys.modules if m.startswith("ai_hats.pipeline.steps")),
    "unknown": unknown,
}))
"""


def _installed_venv(tmp_path: Path) -> Path:
    """Build the wheel from committed content and install it into a fresh venv.

    ``--no-deps`` plus ``pyyaml``: the loader path needs the YAML parser and
    nothing else, and the point of this tier is the wheel's *metadata*, not its
    dependency solve. The venv is the isolation — no ``PYTHONPATH``, no checkout.
    """
    if not network_available():
        venv_unavailable("uv not on PATH — cannot build or install the ai-hats wheel")
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

    venv = tmp_path / "venv"
    made = subprocess.run(
        ["uv", "venv", str(venv)],
        cwd=str(tmp_path),
        env=clean_env(),
        capture_output=True,
        text=True,
        timeout=BUILD_TIMEOUT_S,
    )
    assert made.returncode == 0, f"uv venv failed:\n{made.stdout}\n{made.stderr}"

    env = clean_env()
    env["VIRTUAL_ENV"] = str(venv)
    installed = subprocess.run(
        ["uv", "pip", "install", "--no-deps", str(wheels[0]), "pyyaml"],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=BUILD_TIMEOUT_S,
    )
    assert installed.returncode == 0, (
        f"uv pip install failed:\n{installed.stdout}\n{installed.stderr}"
    )
    return venv


@pytest.fixture(scope="module")
def probed(tmp_path_factory) -> dict:
    """One build + install + probe for the whole file.

    Measured at ~1.2 s end to end (uv build 0.6 s), so the module scope is for the
    shared *result*, not for a cost worth avoiding.
    """
    work = tmp_path_factory.mktemp("step-entry-points")
    venv = _installed_venv(work)
    done = subprocess.run(
        [str(venv / "bin" / "python"), "-c", PROBE, STEP_ID],
        cwd=str(work),
        env=clean_env(),
        capture_output=True,
        text=True,
        timeout=PROBE_TIMEOUT_S,
    )
    assert done.returncode == 0, (
        "the installed ai-hats could not resolve a built-in step — this is the "
        'failure a missing `[project.entry-points."ai_hats.steps"]` block produces, '
        f"and nothing in the source tree sees it:\n{done.stdout}\n{done.stderr}"
    )
    return json.loads(done.stdout)


def test_a_real_install_resolves_a_built_in_step_through_its_metadata(probed: dict) -> None:
    result = probed

    assert STEP_ID in result["advertised"], (
        f"the installed wheel advertises {result['advertised']} under `ai_hats.steps` — "
        "the built-in step declarations did not reach the distribution's metadata "
        '(check `[project.entry-points."ai_hats.steps"]` in pyproject.toml)'
    )
    assert len(result["advertised"]) == 23, (
        f"the wheel advertises {len(result['advertised'])} step ids, not 23: {result['advertised']}"
    )
    assert result["steps"] == [STEP_ID], (
        f"the pipeline did not build from its YAML: {result['steps']}"
    )


def test_a_real_install_imports_only_the_steps_the_yaml_names(probed: dict) -> None:
    """The point of the seam, asserted where an editable install cannot fake it."""
    result = probed

    assert result["imported_at_import_time"] == [], (
        "importing the loader pulled in step modules "
        f"{result['imported_at_import_time']} — the registration-by-import edge is "
        "back, and with it the cycle HATS-1783 cut (ADR-0026 D12)"
    )
    assert result["imported_after_load"] == ["ai_hats.pipeline.steps", STEP_MODULE], (
        f"a one-step pipeline imported {result['imported_after_load']} — a pipeline "
        "must cost only the steps its YAML names"
    )


def test_a_real_install_still_refuses_an_unknown_step_id_by_name(probed: dict) -> None:
    """Loud, and it says what IS known — the pre-HATS-1783 behaviour, kept."""
    result = probed

    assert "unknown step: 'no_such_step'" in result["unknown"], result["unknown"]
    assert STEP_ID in result["unknown"], (
        f"the refusal does not name what is registered: {result['unknown']}"
    )
