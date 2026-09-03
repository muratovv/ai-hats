"""e2e (HATS-1700)

flow:   a developer runs the local unit gate repeatedly after using extra provider
        surfaces in the caller virtual environment
cmds:
    bash scripts/gates.sh unit
    bash scripts/gates.sh unit
expect: both runs detect a test-installed provider, ignore caller-only providers, and
        leave the caller virtual environment and checkout unchanged
why: a unit gate that reuses its caller environment can turn the same broken tree green
     after the first run contaminates that environment
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOST_ENTRY_POINT = "hats1700_host_only"
TEST_ENTRY_POINT = "hats1700_test_mutation"
pytestmark = [pytest.mark.integration, pytest.mark.install_heavy]


def _lock_state() -> tuple[bool, int, int] | None:
    """Identity of the project lockfile, for a before/after comparison.

    HATS-1857: the claim is that the unit stage does not TOUCH ``uv.lock``, not
    that the file is absent — ``uv sync``, which `health.py` prints as the
    env-drift remedy, legitimately creates it. Size rides along with mtime so a
    same-second rewrite cannot pass as untouched.
    """
    lock = REPO_ROOT / "uv.lock"
    if not lock.exists():
        return None
    stat = lock.stat()
    return (True, stat.st_mtime_ns, stat.st_size)


def _write_provider_package(root: Path, distribution: str, entry_point: str) -> Path:
    module = distribution.replace("-", "_")
    package = root / distribution
    source = package / module
    source.mkdir(parents=True)
    (source / "__init__.py").write_text("provider = object()\n", encoding="utf-8")
    (package / "pyproject.toml").write_text(
        f"""[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "{distribution}"
version = "0.0.0"

[project.entry-points."ai_hats.providers"]
{entry_point} = "{module}:provider"
""",
        encoding="utf-8",
    )
    return package


def _provider_names(python: Path) -> set[str]:
    probe = subprocess.run(
        [
            str(python),
            "-c",
            "from importlib import metadata; "
            "print('\\n'.join(sorted(ep.name for ep in "
            "metadata.entry_points(group='ai_hats.providers'))))",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return set(probe.stdout.splitlines())


@pytest.fixture(scope="module")
def caller_environment(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("hermetic-unit-gate")
    venv = root / "caller-venv"
    subprocess.run(
        ["uv", "venv", str(venv), "--python", sys.executable],
        check=True,
        capture_output=True,
        timeout=300,
    )
    python = venv / "bin" / "python"
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), "--editable", ".[dev]"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        timeout=600,
    )
    host_provider = _write_provider_package(root, "hats1700-host-provider", HOST_ENTRY_POINT)
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), str(host_provider)],
        check=True,
        capture_output=True,
        timeout=300,
    )
    return python, root


def _write_mutating_test(path: Path) -> None:
    path.write_text(
        """import json
import os
import subprocess
import sys
from importlib import metadata
from pathlib import Path


def test_installs_synthetic_provider():
    names = sorted(ep.name for ep in metadata.entry_points(group="ai_hats.providers"))
    Path(os.environ["HATS1700_SEEN_PROVIDERS"]).write_text(json.dumps(names))
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            sys.executable,
            os.environ["HATS1700_MUTATION_PACKAGE"],
        ],
        check=True,
    )
""",
        encoding="utf-8",
    )


@pytest.fixture
def mutating_test_file() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="hats1700-", dir=REPO_ROOT / "tests") as root:
        path = Path(root) / "test_synthetic_provider_mutation.py"
        _write_mutating_test(path)
        yield path


def _run_unit(
    python: Path, test_file: Path, mutation_package: Path, seen_providers: Path
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "PYTHON": str(python),
            "VIRTUAL_ENV": str(python.parent.parent),
            "VIRTUAL_ENV_PROMPT": "hats1700-caller",
            "PYTHONPATH": str(REPO_ROOT / "src"),
            "HATS1700_MUTATION_PACKAGE": str(mutation_package),
            "HATS1700_SEEN_PROVIDERS": str(seen_providers),
            # A stage runs bare (HATS-1878): the one file to collect rides
            # pytest's own variable, not argv the runner would have to forward.
            "PYTEST_ADDOPTS": str(test_file),
        }
    )
    return subprocess.run(
        ["bash", "scripts/gates.sh", "unit"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )


def test_unit_stage_ignores_caller_provider_set_and_preserves_it(
    caller_environment: tuple[Path, Path],
    mutating_test_file: Path,
) -> None:
    python, root = caller_environment
    mutation_package = _write_provider_package(root, "hats1700-test-provider", TEST_ENTRY_POINT)
    before = _provider_names(python)
    assert HOST_ENTRY_POINT in before
    assert TEST_ENTRY_POINT not in before
    lock_before = _lock_state()

    seen_first = root / "seen-first.json"
    seen_second = root / "seen-second.json"
    first = _run_unit(python, mutating_test_file, mutation_package, seen_first)
    second = _run_unit(python, mutating_test_file, mutation_package, seen_second)

    diagnostics = (
        f"first stdout:\n{first.stdout}\nfirst stderr:\n{first.stderr}\n"
        f"second stdout:\n{second.stdout}\nsecond stderr:\n{second.stderr}"
    )
    assert first.returncode != 0, diagnostics
    assert second.returncode != 0, diagnostics
    assert HOST_ENTRY_POINT not in json.loads(seen_first.read_text(encoding="utf-8"))
    assert HOST_ENTRY_POINT not in json.loads(seen_second.read_text(encoding="utf-8"))
    assert _provider_names(python) == before
    assert _lock_state() == lock_before, (
        "the unit stage touched uv.lock — `uv run --isolated --no-project` must "
        f"leave the project lockfile alone (before={lock_before}, after={_lock_state()})"
    )


def test_unit_stage_attributes_provider_mutation_to_test_nodeid(
    caller_environment: tuple[Path, Path],
    mutating_test_file: Path,
) -> None:
    python, root = caller_environment
    mutation_package = _write_provider_package(
        root, "hats1700-attribution-provider", TEST_ENTRY_POINT
    )
    result = _run_unit(
        python,
        mutating_test_file,
        mutation_package,
        root / "seen-attribution.json",
    )
    output = f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    nodeid = (
        f"{mutating_test_file.relative_to(REPO_ROOT).as_posix()}::test_installs_synthetic_provider"
    )

    assert result.returncode != 0, output
    assert f"[provider-entry-point-integrity] {nodeid}" in output, output
    assert "[dev-env-integrity]" not in output, output
