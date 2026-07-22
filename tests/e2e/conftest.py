"""e2e fixtures — shared across the directory.

* ``requires_claude_auth`` — skip-marker: ``claude`` binary on PATH +
  ``claude --version`` exits 0. Mirrors the probe used by other e2e
  files (test_role_isolation.py, test_subagent_sdk_smoke.py).
* ``repo_root`` — single source of truth for repo path math.
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
from ai_hats.paths import ENV_AI_HATS_VENV, PROJECT_CONFIG
from ai_hats.constants import ENV_REPO_URL

# Make ``_helpers`` importable as a flat package, rooted at tests/e2e/.
# pytest doesn't treat tests/e2e/ as a package (no ``__init__.py`` at
# the tests/ level — keeps regular tests/ flat-discovery happy), so we
# wire sys.path manually rather than relative-import.
sys.path.insert(0, str(Path(__file__).resolve().parent))


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
# HATS-790: the ``bin/ai-hats`` console-script generator was removed, so e2e
# tests can no longer point at a ``<venv>/bin/ai-hats`` executable. The
# ``ai_hats_shim`` session fixture (below) materialises a real shim that execs
# the dev venv's ``python -m ai_hats`` (the editable build under test).


@pytest.fixture(autouse=True)
def _scrub_redirect_env(monkeypatch):
    """HATS-685: strip python/ai_hats *redirect* vars from ``os.environ`` for
    every e2e test, so a subprocess env built via ``os.environ.copy()``
    exercises the REAL installed ``ai_hats`` — not the source tree that an
    inherited ``PYTHONPATH`` (the worktree test workaround, and what
    ``ai-hats wt exec`` sets) would redirect it to. Without this, the launcher's
    ``self init`` subprocess imports ``ai_hats`` (and, post-HATS-876,
    ``ai_hats_library``) from ``src`` instead of the install, so the run no longer
    tests the packaged artefact (pre-HATS-876 this failed loud — built-in roles
    vanished with "Role 'assistant' not found").

    Deliberate ``PYTHONPATH=src`` tests are unaffected: they re-set ``PYTHONPATH``
    explicitly after copying env, so a scrubbed ``os.environ`` yields exactly the
    clean ``PYTHONPATH=src`` they want. Denylist + rationale: ``_helpers/env.py``.
    """
    from _helpers.env import ENV_DENYLIST

    for key in ENV_DENYLIST:
        monkeypatch.delenv(key, raising=False)


# HATS-678 / HATS-771: cap on how many INSTALL-heavy e2e tests (~26 across 21
# files doing a real ``uv pip install``) may run concurrently under the gate's
# ``-n8 --dist=loadgroup``. ``_install_heavy_group_map`` round-robins their
# FILES into this many fixed xdist groups so ``loadgroup`` runs at most
# ``INSTALL_HEAVY_GROUPS`` at once; light tests keep per-file groups.
# Origin: uncapped pip-era index contention caused network-reset / timeout
# flakes (HATS-676). Under uv (HATS-763) the warm global cache makes contention
# near-zero, so the default is relaxed to 8 (inert on the -n8 gate); the cap is
# RETAINED as the cold-cache / degraded-network safety valve — set
# ``AI_HATS_E2E_INSTALL_HEAVY_GROUPS=<lower>`` to re-throttle. Raw A/B numbers:
# HATS-771 work_log.
INSTALL_HEAVY_GROUPS = int(os.environ.get("AI_HATS_E2E_INSTALL_HEAVY_GROUPS", "8"))


def _install_heavy_group_map(install_heavy_files, k):  # noqa: ANN001, ANN202
    """Round-robin sorted install-heavy files into ``k`` fixed xdist groups.

    Returns ``{file: "install_heavy_<n>"}``. Pure + deterministic (``sorted`` →
    stable order → ``i % k``; no clock/random) so it is unit-testable without
    pytest internals — see ``tests/e2e/test_install_heavy_sharding.py``. File
    granularity (not per-test) keeps every test of an install-heavy file in ONE
    group, so a module-scoped own-build fixture (e.g. ``private_launcher``)
    never rebuilds across workers.

    Fail-under-revert: collapse this to per-file groups (drop the call in the
    hook) → install-heavy items fan back out to ``nworkers`` concurrent installs.
    """
    return {
        f: f"install_heavy_{i % k}" for i, f in enumerate(sorted(install_heavy_files))
    }


def pytest_collection_modifyitems(config, items):  # noqa: ANN001, ANN201
    """Assign xdist scheduling groups for ``--dist=loadgroup``.

    Three goals under parallel runs:

    * **live-claude (cohort B) → one worker + a deselect marker.** Every live
      test gates on the ``requires_claude_auth`` fixture; we (a) tag it with a
      real ``live_claude`` marker so ``-m "not live_claude"`` yields a
      deterministic offline / no-auth e2e run (HATS-583), and (b) pin the whole
      cohort to a single ``live_claude`` xdist group so a parallel run never
      opens N concurrent SDK sessions (cost / rate-limit hazard, HATS-589).
    * **install-heavy → ``INSTALL_HEAVY_GROUPS`` capped groups.** Tests tagged
      ``@pytest.mark.install_heavy`` run a real ``uv pip install`` at call time;
      round-robining their files into a small fixed set of groups caps how many
      hit the package index concurrently (HATS-678 — root-fix for the flake
      class HATS-676 quarantined).
    * **everything else → grouped by file.** Mirrors ``--dist=loadfile``
      semantics so module-scoped venv fixtures stay coherent per worker.

    Precedence (xdist groups): live_claude → install_heavy → per-file. The
    ``xdist_group`` assignments are consulted only by the ``loadgroup``
    scheduler — under ``loadfile``, ``-n0`` (serial), or no xdist they are
    inert, so this hook is safe in every run mode. The ``live_claude`` *deselect*
    marker (HATS-583) is the exception: it is a normal marker, honoured by
    ``-m`` selection in every run mode.

    NOTE (HATS-678 Category A): the per-worker session ``_shared_launcher_venv``
    builds still fire uncapped at session start, but install from the LOCAL repo
    and warm the shared cache once — a far narrower window than the capped class.
    """
    group_for_file = _install_heavy_group_map(
        {
            item.nodeid.split("::", 1)[0]
            for item in items
            if item.get_closest_marker("install_heavy")
        },
        INSTALL_HEAVY_GROUPS,
    )
    for item in items:
        if "requires_claude_auth" in getattr(item, "fixturenames", ()):
            # HATS-583: a real ``live_claude`` deselect marker, consulted in
            # EVERY run mode (serial / loadfile / loadgroup), so a deterministic
            # offline run is just ``-m "not live_claude"``...
            item.add_marker(pytest.mark.live_claude)
            # ...plus the xdist scheduling group (loadgroup-only) that pins the
            # whole live cohort to ONE worker — no N concurrent SDK sessions.
            item.add_marker(pytest.mark.xdist_group("live_claude"))
        elif item.get_closest_marker("install_heavy"):
            item.add_marker(
                pytest.mark.xdist_group(group_for_file[item.nodeid.split("::", 1)[0]])
            )
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
def requires_cline_auth() -> None:
    """Skip if ``cline`` binary missing (HATS-1087).

    Probe: ``cline --version`` exits 0. Mirrors ``requires_claude_auth``;
    auth-gated paths surface their own detection inside the cline run envelope.
    """
    if not shutil.which("cline"):
        pytest.skip("cline binary not found in PATH")
    try:
        cp = subprocess.run(
            ["cline", "--version"], capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"cline --version probe failed: {exc}")
    if cp.returncode != 0:
        pytest.skip(f"cline --version exit {cp.returncode}: {cp.stderr[:200]}")


@pytest.fixture(scope="session")
def ai_hats_shim(tmp_path_factory) -> Path:
    """A real ``ai-hats`` executable for e2e tests (HATS-790: no console script).

    Execs the dev venv's ``python -m ai_hats`` — the editable build under test.
    A real file on disk, so it works both for ``Project.run`` AND for tests that
    read ``project.ai_hats_binary`` directly (task-worktree, reflect). Lives in a
    pytest session tmp dir (NOT ``build/``, which wheel-building e2e tests clean).
    Worktree-portable: ``sys.executable`` is pytest's interpreter wherever it runs.
    """
    shim = tmp_path_factory.mktemp("ai-hats-shim") / "ai-hats"
    shim.write_text(f'#!/usr/bin/env bash\nexec "{sys.executable}" -m ai_hats "$@"\n')
    shim.chmod(0o755)
    return shim


@pytest.fixture
def tmp_project(tmp_path: Path, ai_hats_shim: Path):
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
    import subprocess
    subprocess.run(["git", "init", "-b", "master"], cwd=str(project_path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=str(project_path), check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(project_path), check=True)
    subprocess.run(["git", "commit", "-m", "init", "--allow-empty"], cwd=str(project_path), check=True)
    ProjectConfig(provider="claude", library_paths=[]).save(
        project_path / PROJECT_CONFIG
    )
    Assembler(project_path).init()
    return Project(
        path=project_path,
        ai_hats_binary=ai_hats_shim,
    )


@pytest.fixture(scope="session")
def _shared_launcher_venv(tmp_path_factory, repo_root: Path, request):
    """Session-scoped venv build — internal helper for :func:`tmp_venv_project`.

    Builds the launcher + a shared ai-hats venv ONCE per test session
    via :func:`tests.e2e._helpers.venv.build_launcher_venv` (~30-60s on
    cold pip cache). Returns ``(launcher_path, shared_venv_path)``.

    HATS-569: promoted from module to session scope. The 8 consumers of
    :func:`tmp_venv_project` were audited READ-ONLY (each works in its own
    function-scoped ``project_path``), so building once eliminates ~7 redundant
    ~30-60s builds. The no-mutation contract is guarded by
    :func:`test_wave1_venv_tier.test_shared_venv_reused_across_tests`, which
    fails loudly if any test poisons the shared venv.

    HATS-570: a pass-only ``addfinalizer`` ``rmtree``s the heavy work dir when
    no test failed after the venv build (session-scoped delta), else keeps it
    for triage; registered before the skip paths so the empty ``mktemp`` dir is
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
    import subprocess
    subprocess.run(["git", "init", "-b", "master"], cwd=str(project_path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=str(project_path), check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(project_path), check=True)
    subprocess.run(["git", "commit", "-m", "init", "--allow-empty"], cwd=str(project_path), check=True)
    return Project(
        path=project_path, ai_hats_binary=launcher,
        env={
            # HATS-589: per-worker private build source (no-op on serial).
            ENV_REPO_URL: str(build_src(repo_root)),
            ENV_AI_HATS_VENV: str(shared_venv),
        },
    )


@pytest.fixture(scope="session")
def shared_launcher(_shared_launcher_venv, repo_root: Path, tmp_path_factory):
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
    helpers.

    HATS-828: ``env`` is built via
    :func:`_helpers.env.launcher_subprocess_env` — a HERMETIC env, not a raw
    ``os.environ.copy()``. This fixture is **session-scoped**, so it captures
    ``os.environ`` BEFORE the function-scoped autouse scrubs
    (``_scrub_redirect_env`` / ``_isolate_ai_hats_user_home``) ever apply —
    meaning a raw copy would leak an inherited absolute ``PYTHONPATH=<repo>/src``
    (``wt exec`` / worktree-dev) and an unpinned ``AI_HATS_USER_HOME`` straight
    into every raw consumer's subprocess, hiding the built-in roles ("Role
    'assistant' not found"). The helper drops ``ENV_DENYLIST`` (incl.
    ``PYTHONPATH``) and pins an empty ``AI_HATS_USER_HOME``, so every raw
    consumer is isolated by construction. The WRAPPED ``installed_launcher``
    fixtures keep their own ``PYTHONPATH`` pop / ``HOME`` set as harmless
    defense-in-depth.

    The ``env`` dict is shared across the whole session — consumers MUST
    treat it as read-only and copy before mutating. The shared venv MUST NOT
    be mutated destructively; the no-mutation contract is guarded by
    :func:`test_wave1_venv_tier.test_shared_venv_reused_across_tests`.

    Deliberate long fixture contract — noqa: comment-length.
    """
    from _helpers.env import launcher_subprocess_env
    from _helpers.repo_src import build_src

    launcher, shared_venv = _shared_launcher_venv
    env = launcher_subprocess_env(
        os.environ,
        # HATS-589: per-worker private build source (no-op on serial).
        repo_url=build_src(repo_root),
        venv=shared_venv,
        user_home=tmp_path_factory.mktemp("shared-launcher-user-home"),
    )
    return launcher, env, shared_venv
