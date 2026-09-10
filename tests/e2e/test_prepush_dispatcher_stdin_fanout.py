"""e2e (HATS-654)

flow:   a developer pushing git commits with pre-push hook active
cmds:
    git push origin master
expect: pre-push dispatcher reads refs from stdin and fans out verification checks
        across pushed commits
why: without stdin ref fanout, pre-push hooks verify only HEAD commit leaving pushed
     branch history unverified"""

from __future__ import annotations
from _helpers.git import git as _git_helper

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.guards


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DISPATCHER_TEMPLATE = REPO_ROOT / "src/ai_hats/templates/githooks/dispatcher.sh"
ZERO = "0" * 40


DRAIN_HOOK = """#!/usr/bin/env bash
# Mirror git-mastery-pre-push-shared-state.sh: consume ALL of stdin.
set -uo pipefail
while read -r a b c d; do :; done
exit 0
"""

# Mirror quality-gate-pre-push-e2e-master.sh trigger: fire only on a
# master target with a non-zero local sha, then record what we saw to the marker.
MARKER_HOOK = f"""#!/usr/bin/env bash
set -uo pipefail
zero='{ZERO}'
while read -r local_ref local_sha remote_ref remote_sha; do
    [[ -z "${{local_ref:-}}" ]] && continue
    [[ "$remote_ref" != "refs/heads/master" ]] && continue
    [[ "$local_sha" == "$zero" ]] && continue
    printf '%s %s\\n' "$local_ref" "$local_sha" > "$AI_HATS_TEST_MARKER"
    exit 0
done
exit 0
"""


def _git(cwd: Path, *args: str) -> str:
    return _git_helper(cwd, *args).stdout.strip()


def _write_hook(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


def _install_dispatcher(hooks_dir: Path) -> None:
    """Copy the LIVE dispatcher template to ``hooks_dir/pre-push`` (artefact under test)."""
    dispatcher = hooks_dir / "pre-push"
    dispatcher.write_text(DISPATCHER_TEMPLATE.read_text())
    dispatcher.chmod(0o755)


@pytest.fixture
def repo_and_remote(tmp_path: Path) -> tuple[Path, Path]:
    """A work repo on ``master`` wired to a bare ``origin`` remote, one commit in."""
    work = tmp_path / "work"
    bare = tmp_path / "remote.git"
    subprocess.run(
        ["git", "-c", "init.defaultBranch=master", "init", "--bare", "--quiet", str(bare)],
        check=True,
    )
    subprocess.run(
        ["git", "-c", "init.defaultBranch=master", "init", "--quiet", str(work)],
        check=True,
    )
    _git(work, "config", "user.email", "t@e.x")
    _git(work, "config", "user.name", "t")
    _git(work, "remote", "add", "origin", str(bare))
    (work / "f").write_text("1")
    _git(work, "add", "f")
    _git(work, "commit", "-m", "init", "--quiet")
    return work, bare


def _ai_hats_pin() -> dict[str, str]:
    """Env making the installed stub delegate to THIS checkout's ai-hats.

    HATS-1337: the stub is a bootstrap — without a resolvable install it fails
    open and runs nothing, so a fixture that hand-builds `.githooks/` must supply
    one or every assertion below passes vacuously.
    """
    import sys

    from _helpers.env import checkout_pythonpath
    from ai_hats.paths import ENV_AI_HATS_VENV

    return {
        "PYTHONPATH": checkout_pythonpath(REPO_ROOT),
        ENV_AI_HATS_VENV: str(Path(sys.executable).parent.parent),
    }


def _push(work: Path, marker: Path) -> subprocess.CompletedProcess[str]:
    # HATS-887: strip GIT_* so an ambient GIT_DIR can't retarget the push.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["AI_HATS_TEST_MARKER"] = str(marker)
    env.update(_ai_hats_pin())
    return subprocess.run(
        ["git", "push", "origin", "master"],
        cwd=str(work),
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


@pytest.mark.integration
def test_dispatcher_fans_out_stdin_to_each_hook(tmp_path: Path, repo_and_remote):
    """Drainer first, marker second: the marker hook MUST still see the ref protocol."""
    work, _bare = repo_and_remote
    hooks = tmp_path / "hooks"
    (hooks / "pre-push.d").mkdir(parents=True)
    _install_dispatcher(hooks)
    _write_hook(hooks / "pre-push.d" / "00-drain.sh", DRAIN_HOOK)
    _write_hook(hooks / "pre-push.d" / "10-marker.sh", MARKER_HOOK)
    _git(work, "config", "core.hooksPath", str(hooks))
    marker = tmp_path / "marker"

    res = _push(work, marker)

    assert res.returncode == 0, f"push failed:\n{res.stderr}"
    assert marker.exists(), (
        "second pre-push hook never saw stdin — the dispatcher drained the ref "
        "protocol on the first hook and fed EOF to the rest (HATS-654 regression)."
        f"\n{res.stderr}"
    )
    head = _git(work, "rev-parse", "HEAD")
    assert marker.read_text().strip() == f"refs/heads/master {head}", marker.read_text()


@pytest.mark.integration
def test_marker_hook_fires_when_first(tmp_path: Path, repo_and_remote):
    """Control: marker hook alone (no drainer ahead) always sees stdin → GREEN.

    Isolates "marker hook + harness are sound" from "fan-out works", so a failure
    in :func:`test_dispatcher_fans_out_stdin_to_each_hook` is unambiguously the
    fan-out, not the test scaffolding.
    """
    work, _bare = repo_and_remote
    hooks = tmp_path / "hooks"
    (hooks / "pre-push.d").mkdir(parents=True)
    _install_dispatcher(hooks)
    _write_hook(hooks / "pre-push.d" / "10-marker.sh", MARKER_HOOK)
    _git(work, "config", "core.hooksPath", str(hooks))
    marker = tmp_path / "marker"

    res = _push(work, marker)

    assert res.returncode == 0, f"push failed:\n{res.stderr}"
    assert marker.exists(), f"marker hook did not fire even when first:\n{res.stderr}"
