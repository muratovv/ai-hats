"""e2e fixtures — shared across the directory.

* ``requires_claude_auth`` — skip-marker: ``claude`` binary on PATH +
  ``claude --version`` exits 0. Mirrors the probe used by other e2e
  files (test_role_isolation.py, test_subagent_sdk_smoke.py).
* ``repo_root`` — single source of truth for repo path math.
* ``probe_project`` — minimal claude-provider project ready for
  :func:`tests.e2e._helpers.live.live_session`. Function-scoped: tests
  get a fresh tmp project each run. Bakes the ``probe`` role.
* ``tmp_project`` — generic role-less project for subprocess-only
  tests against the ``ai-hats`` CLI. Function-scoped. Returns a
  :class:`tests.e2e._helpers.project.Project`.
* ``tmp_venv_project`` — launcher-tier project backed by a real
  ai-hats venv built once per test module. **Module-scoped** —
  multiple tests in the same file share the venv build (~30-60s)
  and the project dir. Tests must NOT mutate the venv destructively.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# Make ``_helpers`` importable as a flat package, rooted at tests/e2e/.
# pytest doesn't treat tests/e2e/ as a package (no ``__init__.py`` at
# the tests/ level — keeps regular tests/ flat-discovery happy), so we
# wire sys.path manually rather than relative-import.
sys.path.insert(0, str(Path(__file__).resolve().parent))


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Repo checkout root — single source of truth for path math.

    Session-scoped: the value is a process-wide constant, so promoting
    it lets module-scoped fixtures (``tmp_venv_project``) depend on it.
    """
    return REPO_ROOT


@pytest.fixture
def requires_claude_auth() -> None:
    """Skip if ``claude`` binary missing or unauthenticated.

    Probe: ``claude --version`` exits 0. Full auth check would itself
    need network — tests that hit auth-gated paths surface their own
    'Not logged in' detection inside the SDK error envelope.
    """
    if not shutil.which("claude"):
        pytest.skip("claude binary not found in PATH")
    try:
        cp = subprocess.run(
            ["claude", "--version"], capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"claude --version probe failed: {exc}")
    if cp.returncode != 0:
        pytest.skip(f"claude --version exit {cp.returncode}: {cp.stderr[:200]}")


@pytest.fixture
def probe_project(tmp_path: Path) -> Path:
    """A minimal claude-provider ai-hats project ready for ``live_session``.

    Role ``probe`` has no traits/skills/rules — keeps composition cheap
    and the system prompt minimal so tests stay deterministic and cost
    bounded. Tests that need a richer role override by composing one
    inline rather than parametrising this fixture.
    """
    from ai_hats.assembler import Assembler
    from ai_hats.models import ProjectConfig

    project = tmp_path / "project"
    project.mkdir()
    lib = tmp_path / "lib"
    role_dir = lib / "roles" / "probe"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(
        "name: probe\n"
        "priorities: []\n"
        "composition:\n  traits: []\n  rules: []\n  skills: []\n"
        "injection: |\n"
        "  You are a precise, deterministic test agent. Follow the user's\n"
        "  instructions exactly; do not editorialise.\n"
    )
    ProjectConfig(provider="claude", library_paths=[str(lib)]).save(
        project / "ai-hats.yaml"
    )
    asm = Assembler(project)
    asm.init()
    asm.set_role("probe", provider_name="claude")
    return project


@pytest.fixture
def tmp_project(tmp_path: Path, repo_root: Path):
    """Role-less ai-hats project + Project driver for CLI subprocess tests.

    Contract:

    * ``ai-hats.yaml`` written with ``provider: claude`` and an empty
      ``library_paths`` (caller adds entries / roles if needed).
    * ``.agent/ai-hats/`` bootstrapped via :class:`ai_hats.assembler.Assembler`.
      No role set — keeps the project deterministic for tests that
      exercise the CLI surface without an active role.
    * Project's ``ai_hats_binary`` points at the dev venv binary
      (``<repo_root>/.venv/bin/ai-hats``) so tests invoke the local
      checkout, not whatever happens to be on PATH.

    Returns a :class:`tests.e2e._helpers.project.Project`. Use
    :meth:`Project.run` for one-shot ``ai-hats <cmd>`` invocations and
    the fluent ``RunResult.expect_*`` verbs for assertions.
    """
    from ai_hats.assembler import Assembler
    from ai_hats.models import ProjectConfig

    from _helpers.project import Project

    project_path = tmp_path / "project"
    project_path.mkdir()
    ProjectConfig(provider="claude", library_paths=[]).save(
        project_path / "ai-hats.yaml"
    )
    Assembler(project_path).init()
    return Project(
        path=project_path,
        ai_hats_binary=repo_root / ".venv" / "bin" / "ai-hats",
    )


@pytest.fixture(scope="module")
def tmp_venv_project(tmp_path_factory, repo_root: Path):
    """Module-scoped launcher-tier project + real ai-hats venv.

    Builds the launcher + its inner venv ONCE per test module via
    :func:`tests.e2e._helpers.venv.build_launcher_venv` (~30-60s on
    cold pip cache). All tests in the same file reuse the same
    :class:`Project` instance — they MUST NOT mutate the venv
    destructively (deletion of ``.agent/ai-hats/.venv`` will break
    every subsequent test in the module).

    Skips the whole module when:

    * ``scripts/install-launcher.sh`` is missing (untrusted checkout)
    * the launcher install or ``self update`` raises
      :class:`subprocess.CalledProcessError` (typically: no network
      and no warm pip cache for transitive deps)

    Returns a :class:`Project` whose ``ai_hats_binary`` is the
    sandboxed launcher (NOT the dev venv binary used by
    :func:`tmp_project`). The project dir is a sibling of the
    bootstrap dir so ``self init`` operates on a clean slate.
    """
    import subprocess as _subprocess

    from _helpers.project import Project
    from _helpers.venv import build_launcher_venv, network_available

    work = tmp_path_factory.mktemp("hats-venv-tier")
    project_path = work / "project"
    project_path.mkdir()
    if not network_available():
        pytest.skip("pip not on PATH — cannot build launcher venv")
    try:
        launcher = build_launcher_venv(
            work, repo_root, project_dir=project_path,
        )
    except FileNotFoundError as exc:
        pytest.skip(f"install-launcher.sh missing: {exc}")
    except _subprocess.CalledProcessError as exc:
        pytest.skip(
            "launcher venv build failed (likely offline / no warm pip cache); "
            f"stderr tail:\n{(exc.stderr or '')[-400:]}"
        )
    return Project(path=project_path, ai_hats_binary=launcher,
                   env={"AI_HATS_REPO_URL": str(repo_root)})
