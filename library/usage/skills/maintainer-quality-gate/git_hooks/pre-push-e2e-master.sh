#!/usr/bin/env bash
# HATS-550 — pre-push e2e+smoke gate for pushes that include refs/heads/master.
#
# Reads the standard git pre-push stdin protocol:
#   <local_ref> <local_sha> <remote_ref> <remote_sha>
#
# Behavior:
#   * Triggers iff at least one line targets refs/heads/master with a
#     non-zero local_sha (i.e. not a branch deletion).
#   * On trigger: runs `pytest -m "(integration or smoke) and not quarantine"
#     tests/e2e/ tests/smoke/` from the repo root. Exit 0 → allow push. pytest
#     exit 5 (no tests collected) → allow push. Anything else → block
#     with stderr tail.
#   * HATS-589/592: parallelises with pytest-xdist when present (~706s
#     serial → ~200s on a many-core host). Worker count is adaptive —
#     `min(logical_cpus, 8)` (HATS-592) — so the gate scales to the
#     contributor's machine instead of a hardcoded -n4. `--dist=loadgroup`
#     keeps each test file on one worker (module-scoped venv-build fixture
#     coherence) and pins all live-claude tests onto a single worker — see
#     the `pytest_collection_modifyitems` group hook in tests/e2e/conftest.py
#     — so a parallel run never opens N concurrent SDK sessions. The same hook
#     (HATS-678) round-robins the `@pytest.mark.pip_heavy` tests (real pip at
#     call time) into `PIP_HEAVY_GROUPS` fixed groups so at most K hit the
#     package index concurrently — root-fixing the network-reset flake class
#     HATS-676 quarantined (no gate-arg change: `loadgroup` does the capping).
#     Falls back to serial when xdist is absent so a lean dev env never hard-fails.
#   * pytest not on PATH → BLOCK (silent skip would let
#     `pip uninstall pytest` bypass the gate).
#   * HATS-645: exports `AI_HATS_E2E_REQUIRE_VENV=1` so the tier-2 venv
#     fixture fails-closed instead of skipping when it cannot build its
#     venv (offline / cold pip cache). A silent skip there used to pass
#     the gate green and let master ship with real e2e failures — the
#     gate now treats "cannot verify" as "cannot push".
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

# HATS-589: opt into pytest-xdist when the installed pytest reports the
# plugin. Empty-array expansion is written bash-3.2-safe (macOS system bash)
# so `set -u` doesn't trip on the no-xdist fallback path.
#
# HATS-592: worker count is adaptive — `min(logical_cpus, ceiling)` — so the
# gate scales to the contributor's machine instead of a hardcoded -n4 (the
# gate runs LOCALLY on every master push, on hosts from 4-core laptops to
# many-core workstations). `getconf _NPROCESSORS_ONLN` is POSIX, lives in
# /usr/bin (reachable under a minimal PATH), and works on both macOS and
# Linux; `nproc` / `sysctl` are fallbacks; `4` is the floor default if every
# probe fails. Ceiling caps the win past the point where the suite stops
# being sum-bound (the ~107s single-worker `live_claude` floor binds first)
# and limits pip-cache contention under the venv-tier `--force-reinstall`
# builds.
xdist_args=()
if pytest -VV 2>/dev/null | grep -qi xdist; then
    cores="$(getconf _NPROCESSORS_ONLN 2>/dev/null \
             || nproc 2>/dev/null \
             || sysctl -n hw.logicalcpu 2>/dev/null \
             || echo 4)"
    # Guard the arithmetic: a probe that prints non-digits (or nothing)
    # must not break `$(( ))`. Fall back to the conservative default.
    [[ "$cores" =~ ^[0-9]+$ ]] || cores=4
    ceiling=8
    n=$(( cores < ceiling ? cores : ceiling ))
    (( n < 1 )) && n=1
    echo "[e2e-gate] pytest-xdist detected — running -n$n --dist=loadgroup (cores=$cores, cap=$ceiling)" >&2
    xdist_args=(-n"$n" --dist=loadgroup)
else
    echo "[e2e-gate] pytest-xdist absent — running serial" >&2
fi

# HATS-645: arm the tier-2 venv fixture's fail-closed mode. Without this the
# session venv build skips (not fails) when it can't reach the network, pytest
# exits 0, and the gate green-lights a push whose tier-2 e2e never actually ran.
export AI_HATS_E2E_REQUIRE_VENV=1

# HATS-676: deselect quarantined known-flaky tests. The gate runs under
# `-n8 --dist=loadgroup`; a handful of stateful real-pip/shared-venv e2e tests
# fail intermittently under that contention (different test each run) and would
# block clean master pushes despite a sound diff. They are tagged
# `@pytest.mark.quarantine` (a concrete HYP-002 known-flaky registry) and
# excluded HERE only — a normal/solo `pytest` still collects & runs them, and
# each carries a de-flake follow-up. Remove a test's marker once it is fixed.
output=$(
    cd "$repo_root" && \
    pytest -m "(integration or smoke) and not quarantine" tests/e2e/ tests/smoke/ \
           ${xdist_args[@]+"${xdist_args[@]}"} \
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
if echo "$output" | grep -q "AI_HATS_E2E_REQUIRE_VENV"; then
    cat >&2 <<'EOF'

[e2e-gate] The failure above is a FAIL-CLOSED venv-tier skip (HATS-645): the
tier-2 e2e venv could not be built (offline / cold pip cache), so the gate
could not actually run those tests. A skip here used to pass the gate green and
let master ship with real failures — it now blocks. Restore network / warm the
pip cache and retry.
EOF
fi
cat >&2 <<'EOF'

Fix the failing tests before pushing to master, or use
`git push --no-verify` (disables every other pre-push hook too).
EOF
exit 1
