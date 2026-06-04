"""HATS-654 — the ``<event>`` dispatcher must fan out git STDIN to EVERY .d/ hook.

Per ``dev_rule_e2e_gate``: the dispatcher (``AI-HATS-DISPATCHER-MARKER``) is pure
bash that no in-process unit test can meaningfully exercise. This file does a
REAL ``git push`` to a bare remote, driven by the LIVE dispatcher template wired
via ``core.hooksPath``, with TWO ``pre-push.d/`` hooks:

  * ``00-drain.sh``  — consumes ALL of stdin (mirrors git-mastery's
    ``pre-push-shared-state.sh``, which is lexicographically first in the real
    chain and reads the whole ref list).
  * ``10-marker.sh`` — mirrors the maintainer ``pre-push-e2e-master.sh`` trigger
    (master ref + non-zero local sha) and writes a marker file when it fires.

Before HATS-654 the dispatcher ran both hooks sharing ONE stdin, so ``00-drain``
ate the ref protocol and ``10-marker`` read EOF → its ``empty stdin → exit 0``
fast-path fired → the e2e gate silently no-opped on every multi-hook push. The
fix captures stdin once and replays a fresh copy into each hook.

Fail-under-revert: revert the STDIN fan-out in
``src/ai_hats/templates/githooks/dispatcher.sh`` → ``10-marker`` sees empty
stdin → no marker → ``test_dispatcher_fans_out_stdin_to_each_hook`` goes RED.
The companion ``test_marker_hook_fires_when_first`` is a control: it has no
drainer ahead of the marker, so it stays GREEN under the revert — proving the
main test's failure is the fan-out, not the marker logic.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DISPATCHER_TEMPLATE = REPO_ROOT / "src/ai_hats/templates/githooks/dispatcher.sh"
ZERO = "0" * 40


DRAIN_HOOK = """#!/usr/bin/env bash
# Mirror git-mastery-pre-push-shared-state.sh: consume ALL of stdin.
set -uo pipefail
while read -r a b c d; do :; done
exit 0
"""

# Mirror maintainer-quality-gate-pre-push-e2e-master.sh trigger: fire only on a
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
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True,
        capture_output=True, text=True,
    ).stdout.strip()


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


def _push(work: Path, marker: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "push", "origin", "master"],
        cwd=str(work), capture_output=True, text=True, timeout=30,
        env={**os.environ, "AI_HATS_TEST_MARKER": str(marker)},
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
