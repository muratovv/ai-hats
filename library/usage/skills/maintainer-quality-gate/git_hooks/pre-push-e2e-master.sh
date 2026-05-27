#!/usr/bin/env bash
# HATS-550 — pre-push e2e+smoke gate for pushes that include refs/heads/master.
#
# Reads the standard git pre-push stdin protocol:
#   <local_ref> <local_sha> <remote_ref> <remote_sha>
#
# Behavior:
#   * Triggers iff at least one line targets refs/heads/master with a
#     non-zero local_sha (i.e. not a branch deletion).
#   * On trigger: runs `pytest -m "integration or smoke" tests/e2e/
#     tests/smoke/` from the repo root. Exit 0 → allow push. pytest
#     exit 5 (no tests collected) → allow push. Anything else → block
#     with stderr tail.
#   * pytest not on PATH → BLOCK (silent skip would let
#     `pip uninstall pytest` bypass the gate).
#   * No env-var bypass. `git push --no-verify` is the only escape and
#     it disables every other hook too — that's the accepted tradeoff
#     (see SKILL.md "How to bypass").

set -uo pipefail

zero='0000000000000000000000000000000000000000'
trigger=0

while read -r local_ref local_sha remote_ref remote_sha; do
    [[ -z "${local_ref:-}" ]] && continue
    # Only react to master target.
    if [[ "$remote_ref" != "refs/heads/master" ]]; then
        continue
    fi
    # Skip deletion of master (local_sha = zero). Deleting master is
    # already an unusual event handled by other safety layers
    # (shared-state-guard catches force-push variants).
    if [[ "$local_sha" == "$zero" ]]; then
        continue
    fi
    trigger=1
    break
done

if [[ $trigger -eq 0 ]]; then
    exit 0
fi

# Locate repo root so `pytest tests/...` resolves regardless of cwd.
repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$repo_root" ]]; then
    echo "[e2e-gate] could not resolve git repo root — push BLOCKED" >&2
    exit 1
fi

if ! command -v pytest &>/dev/null; then
    cat >&2 <<'EOF'
[e2e-gate] pytest not found on PATH — push to master BLOCKED.

This gate cannot be bypassed by removing pytest. Install the dev
extras (`pip install -e ".[dev]"`) or use `git push --no-verify` if
you knowingly accept the loss of every pre-push safety net.
EOF
    exit 1
fi

echo "[e2e-gate] master push detected — running e2e+smoke suite (no bypass)" >&2

# HATS-568: clean stale wheel-build artefacts from the dev checkout's
# build/ dir. Worktree-tier e2e tests run `pip install` against
# AI_HATS_REPO_URL=<repo_root>; pip's bdist_wheel writes to
# <repo_root>/build/. A leftover ``ai_hats-X.Y.devN.dist-info``
# directory from an earlier (possibly interrupted) build causes
# ``[Errno 17] File exists: build/bdist...dist-info`` failures
# across 5 worktree tests on the next run. Idempotent rm.
if [[ -d "$repo_root/build" ]]; then
    echo "[e2e-gate] cleaning stale $repo_root/build/ (HATS-568)" >&2
    rm -rf "$repo_root/build" 2>/dev/null || true
fi

output=$(
    cd "$repo_root" && \
    pytest -m "integration or smoke" tests/e2e/ tests/smoke/ \
           -q --tb=line --no-header -p no:cacheprovider 2>&1
)
rc=$?

if [[ $rc -eq 0 ]]; then
    exit 0
fi

if [[ $rc -eq 5 ]]; then
    # No tests collected. Defensive: if the folders ever vanish or the
    # markers are renamed, don't permanently brick `git push origin master`.
    echo "[e2e-gate] no tests collected (rc=5) — allowing push" >&2
    exit 0
fi

echo "[e2e-gate] pytest FAILED (rc=$rc) — push to master BLOCKED:" >&2
echo "$output" | tail -40 >&2
cat >&2 <<'EOF'

Fix the failing tests before pushing to master, or use
`git push --no-verify` (disables every other pre-push hook too).
EOF
exit 1
