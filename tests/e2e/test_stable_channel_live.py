"""E2E (LIVE): the stable channel installs the published wheel from the real
PyPI index (HATS-765).

764 built the stable resolver (``ai-hats==<ver>`` spec, fail-loud on a 404) and
unit-tested it against a *stubbed* index, explicitly deferring the live check to
765. This is that live check: a stable-channel ``self update`` resolved against
the **real** PyPI ``ai-hats`` JSON endpoint.

Self-skipping until the name is published. The skip guard is the production
resolver itself (``fetch_latest_stable_version``) — its liveness IS the test's
precondition, so there is no second source of truth to drift:

  * pre-publish (the ``ai-hats`` name has no release) → resolver raises
    ``ChannelResolveError`` (404) → this test SKIPs with a documented reason.
    That is its RED baseline: it stays green-by-skip, never a false pass.
  * epic-close (HATS-762 push-once live-verify, right after the first publish)
    → resolver returns the version → the body runs for real.

Precondition for the body (epic-close): the publish that activates this test
ships the *current* version, so the published release is >= the local-source dev
build installed during ``self init`` — the semver-monotonic downgrade guard
(``_classify_semver_downgrade``) admits the install rather than refusing it. When
the local build is AHEAD of the latest published tag (the normal between-releases
state, incl. release-prep), the body SKIPs: the downgrade guard correctly refuses
that install, which is expected, not a failure.

Per ``dev_rule_e2e_gate``: real ``bash`` + real launcher + real ``uv`` install +
real ``ai-hats`` binary, marked ``integration`` + ``install_heavy`` (a real index
install at call time).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from _helpers.repo_src import build_src
from ai_hats.paths import ENV_AI_HATS_VENV
from ai_hats.constants import ENV_LAUNCHER_DEST, ENV_REPO_URL

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"

pytestmark = pytest.mark.install_heavy  # HATS-678: real index install → capped via conftest


def _run(cmd, *, cwd, env, timeout, expect_exit=0):
    result = subprocess.run(
        cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout,
    )
    if expect_exit is not None and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout[-1500:]}\nstderr:\n{result.stderr[-1500:]}"
        )
    return result


@pytest.mark.integration
def test_e2e_stable_self_update_installs_published_wheel(tmp_path: Path) -> None:
    """A stable-channel ``self update`` installs ``ai-hats==<published>`` from
    the real PyPI index, landing the clean published version in the venv."""
    # Skip guard = the production resolver. Pre-publish this raises (404) → SKIP;
    # post-publish it returns the version the body then asserts on.
    from ai_hats.channel import ChannelResolveError, fetch_latest_stable_version

    try:
        published = fetch_latest_stable_version()
    except ChannelResolveError as exc:
        pytest.skip(
            f"ai-hats not yet published on PyPI ({exc}); HATS-765 epic-close "
            "publish activates this live test"
        )

    # The body asserts a stable `self update` INSTALLS the published wheel — which
    # only holds when the published release is >= the local source build. On a dev
    # checkout AHEAD of the latest published tag (the normal between-releases
    # state, incl. release-prep before the new tag is published) the semver
    # downgrade-guard CORRECTLY refuses the downgrade, so skip rather than fail:
    # the refusal is expected, not a regression. Runs for real only at/after a
    # publish of the current version (published >= local).
    from ai_hats import __version__ as local_version

    try:
        from packaging.version import Version

        local_is_ahead = Version(local_version) > Version(published)
    except Exception:  # noqa: BLE001 — unparseable local ("dev") → don't skip on version grounds
        local_is_ahead = False
    if local_is_ahead:
        pytest.skip(
            f"local build {local_version} is ahead of latest published {published}; "
            "stable self update correctly refuses the downgrade — live test runs "
            "only at/after a release of the current version"
        )

    launcher = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher.parent.mkdir(parents=True)
    project.mkdir()

    env = os.environ.copy()
    env[ENV_LAUNCHER_DEST] = str(launcher)
    # Local source feeds ONLY the launcher heal (gives a working binary); the
    # stable `self update` below installs ai-hats==<published> from PyPI
    # regardless of AI_HATS_REPO_URL (it drives the edge path, not stable).
    env[ENV_REPO_URL] = str(build_src(REPO_ROOT))
    env.pop(ENV_AI_HATS_VENV, None)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=60)

    # self init heals the venv from local source + configures the project on the
    # stable channel (the documented greenfield default — no harness block).
    _run(
        [str(launcher), "self", "init", "-r", "assistant", "-p", "claude"],
        cwd=project, env=env, timeout=600,
    )

    # The live stable path: resolve <published> from the real PyPI index and
    # install ai-hats==<published>. expect_exit=None — assert explicitly below so
    # a 404 failure is distinguished from any other non-zero exit.
    res = _run(
        [str(launcher), "self", "update"],
        cwd=project, env=env, timeout=600, expect_exit=None,
    )
    out = res.stdout + res.stderr

    # Load-bearing live check: the run reached the real index (NOT the 404
    # fail-loud path from channel.fetch_latest_stable_version).
    assert "could not resolve latest stable version from PyPI" not in out, out
    # HATS-916: the version pre-guard reads ai_hats.__version__, which can be
    # stale editable metadata — the refusal itself is the authoritative
    # local-is-ahead signal, and the expected between-releases outcome.
    if res.returncode != 0 and "Refusing to downgrade" in out:
        pytest.skip(
            "installed dev build is ahead of the latest published tag; "
            "stable self update correctly refuses the downgrade"
        )
    # Epic-close happy path: published >= installed dev build → install proceeds.
    assert res.returncode == 0, f"stable self update failed:\n{out[-1500:]}"

    # The managed install landed the clean published version in the venv.
    ver = _run([str(launcher), "--version"], cwd=project, env=env, timeout=60)
    assert published in ver.stdout, (
        f"expected published {published} in `--version`, got: {ver.stdout!r}"
    )
