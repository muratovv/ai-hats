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
  ai-hats venv built once per test module. **Function-scoped Project
  on top of a module-scoped venv build** — multiple tests in the
  same file share the (~30-60s) venv build cost, but each test
  receives a fresh project directory that points at the shared venv
  via ``AI_HATS_VENV``. Tests can mutate their own project freely;
  they MUST NOT mutate the shared venv (no rm -rf, no pip uninstall).
"""

from __future__ import annotations

import os
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
# Dev-venv ``ai-hats`` binary resolved via ``sys.executable``. Works
# from both the main checkout and from linked git worktrees (where
# ``<worktree>/.venv`` does not exist) — pytest is always launched by
# the dev venv's python, so its sibling ``ai-hats`` binary is the same
# editable build we're testing. HATS-552: previously hardcoded as
# ``repo_root / ".venv" / "bin" / "ai-hats"`` which silently broke
# every ``tmp_project``-using e2e when run from a worktree.
AI_HATS_BINARY = Path(sys.executable).parent / "ai-hats"


def pytest_collection_modifyitems(config, items):  # noqa: ANN001, ANN201
    """HATS-589: assign xdist scheduling groups for ``--dist=loadgroup``.

    Two goals under parallel runs:

    * **live-claude (cohort B) → one worker.** Every live test gates on the
      ``requires_claude_auth`` fixture; we pin them all to a single
      ``live_claude`` group so a parallel run never opens N concurrent SDK
      sessions (cost / rate-limit hazard flagged in HATS-589).
    * **everything else → grouped by file.** Mirrors ``--dist=loadfile``
      semantics so module-scoped venv fixtures stay coherent per worker.

    Markers are only consulted by the ``loadgroup`` scheduler — under
    ``loadfile``, ``-n0`` (serial), or no xdist they are inert, so this hook
    is safe in every run mode.
    """
    for item in items:
        if "requires_claude_auth" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.xdist_group("live_claude"))
        else:
            item.add_marker(pytest.mark.xdist_group(item.nodeid.split("::", 1)[0]))


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Repo checkout root — single source of truth for path math.

    Session-scoped: the value is a process-wide constant, so promoting
    it lets module-scoped fixtures (``tmp_venv_project``) depend on it.
    """
    return REPO_ROOT


def pytest_configure(config):  # noqa: ANN001, ANN201
    """Remove stale wheel-build artefacts from ``<REPO_ROOT>/build/`` once.

    HATS-568: worktree-tier e2e tests run ``pip install`` against
    ``AI_HATS_REPO_URL=<repo_root>``; pip's bdist_wheel writes into
    ``<repo_root>/build/``. A leftover ``ai_hats-X.Y.devN.dist-info``
    directory from a prior (often interrupted) wheel build causes
    ``[Errno 17] File exists: build/bdist...dist-info`` on the next run.

    HATS-589: this MUST be a ``pytest_configure`` hook, not a session-autouse
    fixture. Under ``pytest -n>1`` the xdist controller never executes test
    items, so a session fixture only ever fires on the *workers* — which now
    build in private clones (:func:`_helpers.repo_src.build_src`) and must NOT
    concurrently rmtree the shared dir. ``pytest_configure`` runs on the
    controller (and on the serial process) BEFORE any worker spawns, so the
    shared ``build/`` is cleaned exactly once, by the one process that should
    do it. Workers (``PYTEST_XDIST_WORKER`` set) skip — they own no shared
    state. Idempotent; cost is one ``rmtree`` (<50ms).
    """
    import os
    import shutil

    if os.environ.get("PYTEST_XDIST_WORKER"):
        return
    build_dir = REPO_ROOT / "build"
    if build_dir.exists():
        shutil.rmtree(build_dir, ignore_errors=True)


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
def tmp_project(tmp_path: Path):
    """Role-less ai-hats project + Project driver for CLI subprocess tests.

    Contract:

    * ``ai-hats.yaml`` written with ``provider: claude`` and an empty
      ``library_paths`` (caller adds entries / roles if needed).
    * ``.agent/ai-hats/`` bootstrapped via :class:`ai_hats.assembler.Assembler`.
      No role set — keeps the project deterministic for tests that
      exercise the CLI surface without an active role.
    * Project's ``ai_hats_binary`` points at the dev venv binary
      (resolved via ``sys.executable``'s sibling, NOT the launcher on
      PATH) so tests invoke the local checkout. Worktree-portable.

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
        ai_hats_binary=AI_HATS_BINARY,
    )


@pytest.fixture(scope="session")
def _shared_launcher_venv(tmp_path_factory, repo_root: Path, request):
    """Session-scoped venv build — internal helper for :func:`tmp_venv_project`.

    Builds the launcher + a shared ai-hats venv ONCE per test session
    via :func:`tests.e2e._helpers.venv.build_launcher_venv` (~30-60s on
    cold pip cache). Returns ``(launcher_path, shared_venv_path)``.

    HATS-569: promoted from module scope to session scope. The 8
    consumers of :func:`tmp_venv_project` were audited and none mutate
    the shared venv (no ``rm -rf`` / ``pip uninstall`` / ``self bump``
    to a different version) — each test works in its own
    function-scoped ``project_path`` and only reads/executes the shared
    venv. Building once per session instead of once per module
    eliminates ~7 redundant ~30-60s builds. The no-mutation contract
    is guarded by
    :func:`test_wave1_venv_tier.test_shared_venv_reused_across_tests`,
    which fails loudly if any test poisons the shared venv.

    HATS-570: the ``hats-venv-tier`` work dir holds a full venv (heavy)
    and pytest's default retention keeps only the last few runs, so the
    rest leak (16GB observed). A pass-only finalizer ``rmtree``s the work
    dir when no test failed after the venv was built (session-scoped
    delta — robust to unrelated pre-existing failures); a run with
    venv-tier failures keeps the venv for triage (mirrors the
    :func:`tests.conftest._wt_sandbox` contract). Registered via
    ``addfinalizer`` BEFORE the skip paths so the empty ``mktemp`` dir is
    reclaimed on skip too.

    Skips the whole session when:

    * ``scripts/install-launcher.sh`` is missing (untrusted checkout)
    * the launcher install or ``self update`` raises
      :class:`subprocess.CalledProcessError` (typically: no network
      and no warm pip cache for transitive deps)
    * the launcher / venv artefacts don't materialise as expected
      (raises :class:`RuntimeError` from the helper)
    """
    import subprocess as _subprocess

    from _helpers.venv import (
        build_launcher_venv,
        network_available,
        venv_unavailable,
    )

    work = tmp_path_factory.mktemp("hats-venv-tier")
    failed_before = request.session.testsfailed

    def _finalize() -> None:
        # Pass-only: reclaim the venv work dir iff no test failed after the
        # venv was built (session-scoped delta — ignores unrelated
        # pre-existing failures). A venv-tier failure keeps it for triage.
        if request.session.testsfailed == failed_before:
            shutil.rmtree(work, ignore_errors=True)
        else:
            print(f"\n[venv-tier] failures after build — venv preserved: {work}")

    request.addfinalizer(_finalize)

    # HATS-645: every "cannot build the venv" exit routes through
    # ``venv_unavailable`` — a graceful ``skip`` locally, but a ``fail`` when the
    # master gate exports AI_HATS_E2E_REQUIRE_VENV=1 (a silent skip there is the
    # false-green that let master ship with real e2e failures).
    if not network_available():
        venv_unavailable("pip not on PATH — cannot build launcher venv")
    try:
        return build_launcher_venv(work, repo_root)
    except FileNotFoundError as exc:
        venv_unavailable(f"install-launcher.sh missing: {exc}")
    except _subprocess.TimeoutExpired as exc:
        # HATS-582: the venv build exceeded its (generous) window — almost
        # always a degraded host (offline mid-build, or a corrupted pip
        # cache refetching every dep with retry backoff). Degrade the venv
        # tier rather than ERROR-ing every dependent test: the shared venv is
        # now a single point of failure for ~17 files, so a stuck build must
        # degrade, not cascade.
        detail = getattr(exc, "stderr", None) or str(exc)
        if isinstance(detail, bytes):
            detail = detail.decode(errors="replace")
        venv_unavailable(
            "launcher venv build timed out (degraded host / corrupted pip "
            f"cache); detail tail:\n{detail[-400:]}"
        )
    except (_subprocess.CalledProcessError, RuntimeError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        venv_unavailable(
            "launcher venv build failed (likely offline / no warm pip cache); "
            f"detail tail:\n{detail[-400:]}"
        )


@pytest.fixture
def tmp_venv_project(tmp_path: Path, _shared_launcher_venv, repo_root: Path):
    """Function-scoped launcher-tier project on a session-shared venv.

    Each test gets a fresh project directory; the underlying
    ai-hats venv is built once per session (see
    :func:`_shared_launcher_venv`) and reached via the
    ``AI_HATS_VENV`` env knob, so tests can mutate their project
    freely without poisoning siblings across the session.

    The shared venv MUST NOT be mutated destructively — no
    ``rm -rf <venv>``, no ``pip uninstall``, no ``self bump`` to a
    different ai-hats version. Tests that need to break the venv
    should declare their own function-scoped builder instead.

    Returns a :class:`Project` whose ``ai_hats_binary`` is the
    sandboxed launcher (NOT the dev venv binary used by
    :func:`tmp_project`).
    """
    from _helpers.project import Project
    from _helpers.repo_src import build_src

    launcher, shared_venv = _shared_launcher_venv
    project_path = tmp_path / "project"
    project_path.mkdir()
    return Project(
        path=project_path, ai_hats_binary=launcher,
        env={
            # HATS-589: per-worker private build source (no-op on serial).
            "AI_HATS_REPO_URL": str(build_src(repo_root)),
            "AI_HATS_VENV": str(shared_venv),
        },
    )


@pytest.fixture(scope="session")
def shared_launcher(_shared_launcher_venv, repo_root: Path):
    """Session-scoped ``(launcher, env, shared_venv)`` for raw-subprocess tests.

    HATS-582: the read-only own-venv-builder e2e tests used to each build
    their own launcher venv — either in a module-scoped ``installed_launcher``
    fixture (~165s across 6 files in SETUP) or inline in the test body (~240s
    across ~11 task/wt/config files in CALL). All were audited READ-ONLY on
    the venv (HATS-574): their bump/migrate/task/wt ops target fresh
    ``tmp_path`` project dirs, never the venv. This fixture consolidates them
    onto the single session venv from :func:`_shared_launcher_venv`.

    Returns ``(launcher, env, shared_venv)`` — the exact shape the migrated
    tests' local fixtures returned, so test bodies keep their
    ``launcher, env, venv = ...`` unpacking and their raw ``subprocess``
    helpers. ``env`` is a NEUTRAL copy of ``os.environ`` with
    ``AI_HATS_REPO_URL`` + ``AI_HATS_VENV`` set (no HOME isolation /
    PYTHONPATH pop — matches the prior behaviour of the 5 simple module
    fixtures and :func:`tmp_venv_project`). Tests that need extra env hygiene
    (e.g. ``test_pretooluse_hook_materialization``) copy this dict and add
    their own keys.

    The ``env`` dict is shared across the whole session — consumers MUST
    treat it as read-only and copy before mutating. The shared venv MUST NOT
    be mutated destructively; the no-mutation contract is guarded by
    :func:`test_wave1_venv_tier.test_shared_venv_reused_across_tests`.
    """
    from _helpers.repo_src import build_src

    launcher, shared_venv = _shared_launcher_venv
    env = os.environ.copy()
    # HATS-589: per-worker private build source (no-op on serial).
    env["AI_HATS_REPO_URL"] = str(build_src(repo_root))
    env["AI_HATS_VENV"] = str(shared_venv)
    env.pop("AI_HATS_LAUNCHER_DEST", None)
    return launcher, env, shared_venv
