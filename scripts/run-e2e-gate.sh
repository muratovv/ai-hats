#!/usr/bin/env bash
# Earn the push gate here — what the master pre-push hook demands before it
# lets a push through. The gate itself is the hook's `--run`: `scripts/gates.sh
# run` of the stages the hook declares, in place when this checkout is clean,
# in a scratch checkout of the commit otherwise. What this wrapper adds is the
# housekeeping the heavy tier wants first, and one switch the venv tier reads.
#
#   scripts/run-e2e-gate.sh              # judge HEAD
#   scripts/run-e2e-gate.sh --rev <sha>  # judge one commit
set -uo pipefail

repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" || {
    echo "[run-e2e-gate] not inside a git repository" >&2
    exit 70
}
cd "$repo_root" || exit 70

hook="$repo_root/packages/ai-hats-library/src/ai_hats_library/ai-hats-dev/skills/quality-gate/git_hooks/pre-push-e2e-master.sh"
if [[ ! -f "$hook" ]]; then
    echo "[run-e2e-gate] no push gate at $hook — this checkout does not carry it" >&2
    exit 70
fi

# Stale wheel-build artefacts: the worktree-tier e2e tests `pip install` against
# the repo and write to build/; a leftover dist-info makes the next run fail
# with "File exists". Idempotent.
if [[ -d "$repo_root/build" ]]; then
    echo "[run-e2e-gate] cleaning stale $repo_root/build/" >&2
    rm -rf "$repo_root/build" 2>/dev/null || true
fi

# Reap what killed runs left in TMPDIR before the heavy tier needs the space.
# Deletes only on proof of death; AI_HATS_E2E_CLEAN_TMP=1 escalates to --force.
sweep="$repo_root/scripts/clean-tmp-cruft.sh"
if [[ -x "$sweep" ]]; then
    if [[ "${AI_HATS_E2E_CLEAN_TMP:-0}" == "1" ]]; then
        echo "[run-e2e-gate] sweeping stale tmp cruft --force" >&2
        bash "$sweep" --force >&2 || true
    else
        echo "[run-e2e-gate] reaping provably-dead tmp cruft:" >&2
        bash "$sweep" >&2 || true
    fi
fi

# Arm the venv tier's fail-closed mode: a tier that could not build its venv
# is red, never a skip that reads as green.
export AI_HATS_E2E_REQUIRE_VENV=1

exec bash "$hook" --run "$@"
