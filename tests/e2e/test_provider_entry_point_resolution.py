"""e2e (HATS-1826)

flow:   a user installs the released ai-hats wheel and asks for a surface by name;
        `claude` has to resolve, and it has to resolve ONLY through the installed
        distribution's entry-point metadata
cmds:
    uv build --wheel --out-dir <tmp>/wheels <per-worker clone of the repo>
    uv venv <tmp>/venv && uv pip install --no-deps <wheel>
    <tmp>/venv/bin/python -c "get_provider('claude')"
expect: the installed dist advertises `claude` under `ai_hats.providers` and the
        registry resolves it; a wheel built without that one declaration cannot
        resolve it at all, and says so naming what is available
why:    `claude` used to self-register in `providers._register_builtins` before
        entry-point discovery ran, so its declaration in pyproject.toml was never
        exercised — a broken or missing one would have gone unnoticed in every
        tier. The second test is the one that holds the change: restore the
        built-in registration and it goes red, because claude resolves again from
        a wheel that does not declare it
"""  # comment-length: allow — the e2e catalog header format

from __future__ import annotations

import json
import shutil
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

#: The declaration under test, verbatim from the root pyproject.
CLAUDE_DECLARATION = 'claude = "ai_hats.surfaces.claude.provider:ClaudeProvider"'

#: Runs inside the installed venv and reports; every assertion is made by the
#: test, so a probe failure is legible rather than a bare non-zero exit.
PROBE = """
import importlib.metadata, json

advertised = sorted(ep.name for ep in importlib.metadata.entry_points(group="ai_hats.providers"))

from ai_hats.providers import UnknownProviderError, get_provider

resolved, refusal = "", ""
try:
    resolved = type(get_provider("claude")).__name__
except UnknownProviderError as exc:
    refusal = str(exc)

print(json.dumps({"advertised": advertised, "resolved": resolved, "refusal": refusal}))
"""


def _wheel_from(src: Path, tmp_path: Path, name: str) -> Path:
    wheeldir = tmp_path / f"wheels-{name}"
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
    return wheels[0]


def _probe_installed(wheel: Path, tmp_path: Path, name: str) -> dict:
    """Install ``wheel`` into a fresh venv and report what the registry does there.

    ``--no-deps``: the point is the wheel's *metadata*, not its dependency solve,
    and the probe imports only the registry. The venv is the isolation — no
    ``PYTHONPATH``, no checkout on the path.
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
    installed = subprocess.run(
        ["uv", "pip", "install", "--no-deps", str(wheel)],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=BUILD_TIMEOUT_S,
    )
    assert installed.returncode == 0, (
        f"uv pip install failed:\n{installed.stdout}\n{installed.stderr}"
    )

    done = subprocess.run(
        [str(venv / "bin" / "python"), "-c", PROBE],
        cwd=str(tmp_path),
        env=clean_env(),
        capture_output=True,
        text=True,
        timeout=PROBE_TIMEOUT_S,
    )
    assert done.returncode == 0, f"probe failed:\n{done.stdout}\n{done.stderr}"
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def declared(tmp_path_factory) -> dict:
    """The wheel as it ships."""
    if not network_available():
        venv_unavailable("uv not on PATH — cannot build or install the ai-hats wheel")
    work = tmp_path_factory.mktemp("provider-entry-points")
    return _probe_installed(_wheel_from(build_src(REPO_ROOT), work, "declared"), work, "declared")


@pytest.fixture(scope="module")
def undeclared(tmp_path_factory) -> dict:
    """The same wheel with claude's one declaration deleted from pyproject.toml."""
    if not network_available():
        venv_unavailable("uv not on PATH — cannot build or install the ai-hats wheel")
    work = tmp_path_factory.mktemp("provider-entry-points-undeclared")
    src = work / "src"
    shutil.copytree(build_src(REPO_ROOT), src, symlinks=True, ignore=shutil.ignore_patterns(".git"))
    pyproject = src / "pyproject.toml"
    text = pyproject.read_text()
    assert text.count(CLAUDE_DECLARATION) == 1, (
        f"expected exactly one {CLAUDE_DECLARATION!r} in {pyproject}"
    )
    pyproject.write_text(text.replace(f"{CLAUDE_DECLARATION}\n", "", 1))
    return _probe_installed(_wheel_from(src, work, "undeclared"), work, "undeclared")


def test_the_installed_wheel_advertises_claude_and_resolves_it(declared: dict) -> None:
    assert "claude" in declared["advertised"], (
        f"the built wheel advertises {declared['advertised']} under ai_hats.providers — "
        "claude's declaration did not reach entry_points.txt"
    )
    assert declared["resolved"] == "ClaudeProvider", declared["refusal"]


def test_claude_does_not_resolve_from_a_wheel_that_does_not_declare_it(undeclared: dict) -> None:
    """The negative that holds the change (HATS-1826).

    Nothing in the source tree may register claude behind the declaration's back.
    Restoring `providers._register_builtins` turns this red: claude would resolve
    from a distribution whose metadata never mentions it.
    """
    assert "claude" not in undeclared["advertised"], (
        "the undeclared wheel still advertises claude — the fixture did not remove it"
    )
    assert undeclared["resolved"] == "", (
        f"claude resolved to {undeclared['resolved']!r} from a wheel that does not "
        "declare it — something registers it outside the entry-point group"
    )
    assert "Unknown provider: claude" in undeclared["refusal"]
