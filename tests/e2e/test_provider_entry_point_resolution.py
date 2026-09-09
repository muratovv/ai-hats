"""e2e (HATS-1826)

flow:   a user installs the released ai-hats wheel and asks for a surface by name;
        `claude` has to resolve, and it has to resolve ONLY through the installed
        distribution's entry-point metadata
cmds:
    uv build --wheel --out-dir <tmp>/wheels <per-worker clone of the repo>
    uv venv <tmp>/venv && uv pip install --no-deps <wheel>
    <tmp>/venv/bin/python -c "get_surface('claude')"
expect: the installed dist advertises `claude` under `ai_hats.providers`, the
        registry resolves it, and the probe proves it read the wheel built here
        rather than some release resolved from the index
why:    `claude` used to self-register in `providers._register_builtins` before
        entry-point discovery ran, so its declaration in pyproject.toml was never
        exercised, and a broken or missing one would have gone unnoticed in every
        tier. The other half of the claim — that NOTHING registers claude behind
        the declaration's back — is structural and lives in
        tests/test_area_boundary.py, whose surfaces pin is empty: no shipped
        module may name a surface implementation at all (HATS-1826)
"""  # comment-length: allow — the e2e catalog header format

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from _helpers.env import clean_env  # noqa: E402
from _helpers.repo_src import build_src  # noqa: E402
from _helpers.venv import network_available, venv_unavailable  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.install]

REPO_ROOT = Path(__file__).resolve().parents[2]

BUILD_TIMEOUT_S = 180
PROBE_TIMEOUT_S = 120

#: The declaration under test, as pyproject writes it and as the installed
#: ``entry_points.txt`` spells the same line.
CLAUDE_DECLARATION = 'claude = "ai_hats.surfaces.claude.provider:ClaudeSurface"'
CLAUDE_ENTRY_POINT = "claude = ai_hats.surfaces.claude.provider:ClaudeSurface"

#: Runs inside the installed venv and reports; every assertion is made by the
#: test, so a probe failure is legible rather than a bare non-zero exit.
PROBE = """
import importlib.metadata, json

# The wheel under test, or nothing: an install that resolved `ai-hats` from the
# index instead would otherwise report THAT release's behaviour as this one's.
version = importlib.metadata.version("ai-hats")

advertised = sorted(ep.name for ep in importlib.metadata.entry_points(group="ai_hats.providers"))

from ai_hats.surface_registry import UnknownSurfaceError, get_surface

resolved, refusal = "", ""
try:
    resolved = type(get_surface("claude")).__name__
except UnknownSurfaceError as exc:
    refusal = str(exc)

print(json.dumps({
    "version": version,
    "advertised": advertised,
    "resolved": resolved,
    "refusal": refusal,
}))
"""


def _wheel_from(src: Path, tmp_path: Path, name: str) -> Path:
    env = clean_env()
    wheeldir = tmp_path / f"wheels-{name}"
    built = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(wheeldir), str(src)],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=BUILD_TIMEOUT_S,
    )
    assert built.returncode == 0, f"uv build failed:\n{built.stdout}\n{built.stderr}"
    wheels = sorted(wheeldir.glob("ai_hats-*.whl"))
    assert wheels, f"no ai-hats wheel built under {wheeldir}"
    return wheels[0]


#: What importing the registry drags in. First-party siblings come from the same
#: tree as the wheel (their published floors do not resolve from the index — the
#: skip HATS-1717 records), third-party ones from the index like any user's install.
FIRST_PARTY_SIBLINGS = (
    "packages/ai-hats-core",
    "packages/ai-hats-observe",
    "packages/ai-hats-wt",
    "packages/ai-hats-rack",
)
THIRD_PARTY = ("pyyaml", "click", "rich", "pydantic", "filelock")


def _install(wheel: Path, src: Path, tmp_path: Path, name: str) -> Path:
    """Install ``wheel`` into a fresh venv and return it.

    ``--no-deps`` for everything first-party: the point is the wheel's *metadata*,
    not its dependency solve. The venv is the isolation — no ``PYTHONPATH``, no
    checkout on the path.
    """
    venv = tmp_path / f"venv-{name}"
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
    first_party = [str(wheel), *(str(src / rel) for rel in FIRST_PARTY_SIBLINGS)]
    # comment-length: allow — the order is the fix, and it is invisible without the why
    # Third-party FIRST, first-party second and with --no-deps: `uv pip install`
    # re-resolves the whole environment, so the other order let the index answer for
    # `ai-hats` and replaced the wheel under test with the published 0.14.0 — the probe
    # then reported that release's behaviour and the test read as a real failure.
    for args in ([*THIRD_PARTY], ["--no-deps", *first_party]):
        installed = subprocess.run(
            ["uv", "pip", "install", *args],
            cwd=str(tmp_path),
            env=env,
            capture_output=True,
            text=True,
            timeout=BUILD_TIMEOUT_S,
        )
        assert installed.returncode == 0, (
            f"uv pip install {args} failed:\n{installed.stdout}\n{installed.stderr}"
        )

    return venv


def _probe(venv: Path, tmp_path: Path, *, version: str = "") -> dict:
    """Ask the installed registry for claude and report what it did."""
    done = subprocess.run(
        [str(venv / "bin" / "python"), "-c", PROBE],
        cwd=str(tmp_path),
        env=clean_env(),
        capture_output=True,
        text=True,
        timeout=PROBE_TIMEOUT_S,
    )
    assert done.returncode == 0, f"probe failed:\n{done.stdout}\n{done.stderr}"
    report = json.loads(done.stdout.strip().splitlines()[-1])
    if version:
        assert report["version"] == version, (
            f"the venv holds ai-hats {report['version']}, not the {version} built here — "
            "something resolved the distribution from the index and this probe would "
            "have reported that release's behaviour"
        )
    return report


@pytest.fixture(scope="module")
def declared_venv(tmp_path_factory) -> tuple[Path, str]:
    """One build + install for the whole file: the wheel as it ships, and its version."""
    if not network_available():
        venv_unavailable("uv not on PATH — cannot build or install the ai-hats wheel")
    work = tmp_path_factory.mktemp("provider-entry-points")
    src = build_src(REPO_ROOT)
    wheel = _wheel_from(src, work, "declared")
    return _install(wheel, src, work, "declared"), wheel.name.split("-")[1]


@pytest.fixture(scope="module")
def declared(declared_venv: tuple[Path, str], tmp_path_factory) -> dict:
    venv, version = declared_venv
    return _probe(venv, tmp_path_factory.mktemp("declared-probe"), version=version)


def test_the_installed_wheel_advertises_claude_and_resolves_it(declared: dict) -> None:
    assert "claude" in declared["advertised"], (
        f"the built wheel advertises {declared['advertised']} under ai_hats.providers — "
        "claude's declaration did not reach entry_points.txt"
    )
    assert declared["resolved"] == "ClaudeSurface", declared["refusal"]
