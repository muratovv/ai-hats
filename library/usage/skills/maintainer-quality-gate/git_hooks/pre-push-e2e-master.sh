#!/usr/bin/env bash
# HATS-550 / HATS-686 — pre-push e2e+smoke gate for pushes that include
# refs/heads/master, DECOUPLED from the push connection (HATS-686).
#
# Two modes (selected by argv):
#
#   1. CHECK MODE (default — git invokes the hook with the standard pre-push
#      protocol on stdin: `<local_ref> <local_sha> <remote_ref> <remote_sha>`).
#      INSTANT: for every line targeting refs/heads/master with a non-zero
#      local_sha, require a green pass-marker keyed to that SHA. All present →
#      allow (exit 0). Any missing → block (exit 1) with the run command. No
#      pytest, no network → finishes well under GitHub's ~30s SSH idle window,
#      which killed the old in-hook 27-min run (HATS-684 — exit 141, twice).
#
#   2. RUN MODE (`--run`, invoked manually / via scripts/run-e2e-gate.sh).
#      Runs `pytest -m "(integration or smoke) and not quarantine"
#      tests/e2e/ tests/smoke/` from the repo root. On pass AND a clean working
#      tree, writes a marker keyed to `git rev-parse HEAD`. A DIRTY tree runs
#      the suite but writes NO marker: the gate builds wheels from the working
#      tree, so the marker is only honest when clean-tree == HEAD content ==
#      the SHA that will be pushed.
#
# Marker store: <git-common-dir>/ai-hats/e2e-gate/<sha> (under .git/, never
# committed, shared across worktrees). Forging a marker (`touch …`) is the
# moral equivalent of `git push --no-verify` — a deliberate local act by the
# trusted maintainer, NOT an accidental normal-flow bypass. See SKILL.md.
#
# Run-mode behaviour carried over from earlier tickets:
#   * HATS-568: sweeps stale `build/` wheel artefacts before the run.
#   * HATS-589/592: parallelises with pytest-xdist when present, adaptive
#     `-n min(logical_cpus, 8) --dist=loadgroup`; serial fallback when absent.
#   * HATS-645: exports `AI_HATS_E2E_REQUIRE_VENV=1` so the tier-2 venv fixture
#     fails-closed instead of skipping when it cannot build its venv.
#   * HATS-676: deselects `@pytest.mark.quarantine` known-flaky tests.
#   * pytest not on PATH in run mode → ABORT (no marker).
#
# No env-var bypass in the normal (check) flow. `git push --no-verify` is the
# only escape and it disables every other hook too — accepted tradeoff.

set -uo pipefail

zero='0000000000000000000000000000000000000000'

# --- shared helpers --------------------------------------------------------

# Resolve the marker directory under the shared .git common dir so a marker
# written in one worktree is visible from any worktree of the same repo.
# Arg 1: a directory inside the repo (defaults to cwd).
marker_dir() {
    local in_dir="${1:-.}"
    local common
    common="$(git -C "$in_dir" rev-parse --git-common-dir 2>/dev/null || true)"
    [[ -z "$common" ]] && return 1
    # `git -C` makes --git-common-dir relative to in_dir; absolutise it.
    case "$common" in
        /*) : ;;
        *) common="$in_dir/$common" ;;
    esac
    common="$(cd "$common" 2>/dev/null && pwd)" || return 1
    printf '%s/ai-hats/e2e-gate' "$common"
}

# --- check mode (default) --------------------------------------------------

check_mode() {
    # Collect master-targeting, non-deletion local_shas from the pre-push
    # protocol on stdin. Newline-accumulator instead of an array so the
    # empty case is safe under bash 3.2 + `set -u` (macOS system bash).
    local local_ref local_sha remote_ref remote_sha
    local need=""
    while read -r local_ref local_sha remote_ref remote_sha; do
        [[ -z "${local_ref:-}" ]] && continue
        [[ "$remote_ref" != "refs/heads/master" ]] && continue
        [[ "$local_sha" == "$zero" ]] && continue
        need="${need}${local_sha}"$'\n'
    done

    # No master target (feature branch, deletion, empty stdin) → fast-path.
    if [[ -z "$need" ]]; then
        exit 0
    fi

    local mdir
    mdir="$(marker_dir ".")" || {
        echo "[e2e-gate] could not resolve git dir — push to master BLOCKED" >&2
        exit 1
    }

    local sha missing=0
    while IFS= read -r sha; do
        [[ -z "$sha" ]] && continue
        if [[ ! -f "$mdir/$sha" ]]; then
            missing=1
            break
        fi
        # Defensive: marker filename must equal the SHA recorded inside it.
        if ! grep -qx "sha=$sha" "$mdir/$sha" 2>/dev/null; then
            missing=1
            break
        fi
    done <<< "$need"

    if [[ $missing -eq 0 ]]; then
        echo "[e2e-gate] green e2e marker present for the master HEAD — push allowed (HATS-686)" >&2
        exit 0
    fi

    cat >&2 <<EOF
[e2e-gate] No green e2e marker for the master commit you are pushing — BLOCKED.

The e2e+smoke suite now runs OUT OF BAND (HATS-686): GitHub closes the push
SSH connection ~30s in, so a ~27-min suite can no longer run inside pre-push.
Run the gate on this exact HEAD, then push:

    scripts/run-e2e-gate.sh        # (or: bash "$0" --run)

On green it writes a marker keyed to HEAD's SHA and this push passes instantly.
To knowingly skip every pre-push hook: git push --no-verify.
EOF
    exit 1
}

# --- run mode (`--run`) ----------------------------------------------------

run_mode() {
    local repo_root
    repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
    if [[ -z "$repo_root" ]]; then
        echo "[e2e-gate] could not resolve git repo root — gate ABORTED" >&2
        exit 1
    fi

    if ! command -v pytest &>/dev/null; then
        cat >&2 <<'EOF'
[e2e-gate] pytest not found on PATH — gate ABORTED, no marker written.

Install the dev extras (`pip install -e ".[dev]"`) so the gate can run.
EOF
        exit 1
    fi

    echo "[e2e-gate] running e2e+smoke suite out of band (HATS-686, no bypass)" >&2

    # HATS-568: clean stale wheel-build artefacts (worktree-tier e2e tests
    # `pip install` against the repo and write to build/; a leftover dist-info
    # dir causes "File exists" failures on the next run). Idempotent rm.
    if [[ -d "$repo_root/build" ]]; then
        echo "[e2e-gate] cleaning stale $repo_root/build/ (HATS-568)" >&2
        rm -rf "$repo_root/build" 2>/dev/null || true
    fi

    # HATS-589/592: opt into pytest-xdist when present, adaptive worker count
    # `min(logical_cpus, ceiling)`. bash-3.2-safe empty-array expansion.
    local -a xdist_args=()
    if pytest -VV 2>/dev/null | grep -qi xdist; then
        local cores ceiling n
        cores="$(getconf _NPROCESSORS_ONLN 2>/dev/null \
                 || nproc 2>/dev/null \
                 || sysctl -n hw.logicalcpu 2>/dev/null \
                 || echo 4)"
        [[ "$cores" =~ ^[0-9]+$ ]] || cores=4
        ceiling=8
        n=$(( cores < ceiling ? cores : ceiling ))
        (( n < 1 )) && n=1
        echo "[e2e-gate] pytest-xdist detected — running -n$n --dist=loadgroup (cores=$cores, cap=$ceiling)" >&2
        xdist_args=(-n"$n" --dist=loadgroup)
    else
        echo "[e2e-gate] pytest-xdist absent — running serial" >&2
    fi

    # HATS-645: arm the tier-2 venv fixture's fail-closed mode.
    export AI_HATS_E2E_REQUIRE_VENV=1

    # HATS-676: deselect quarantined known-flaky tests (they still run under a
    # normal/solo `pytest`; excluded only under the gate's contention).
    local output rc
    output=$(
        cd "$repo_root" && \
        pytest -m "(integration or smoke) and not quarantine" tests/e2e/ tests/smoke/ \
               ${xdist_args[@]+"${xdist_args[@]}"} \
               -q --tb=line --no-header -p no:cacheprovider 2>&1
    )
    rc=$?

    if [[ $rc -ne 0 && $rc -ne 5 ]]; then
        echo "[e2e-gate] pytest FAILED (rc=$rc) — NO marker written:" >&2
        echo "$output" | tail -40 >&2
        if echo "$output" | grep -q "AI_HATS_E2E_REQUIRE_VENV"; then
            cat >&2 <<'EOF'

[e2e-gate] The failure above is a FAIL-CLOSED venv-tier skip (HATS-645): the
tier-2 e2e venv could not be built (offline / cold pip cache), so the gate
could not actually run those tests. Restore network / warm the pip cache and
re-run the gate.
EOF
        fi
        cat >&2 <<'EOF'

Fix the failing tests, then re-run the gate. (To push without any gate:
git push --no-verify — disables every pre-push hook too.)
EOF
        exit 1
    fi

    if [[ $rc -eq 5 ]]; then
        # No tests collected. Defensive: a renamed marker or empty folder must
        # not permanently brick `git push origin master` (HATS-550 invariant).
        echo "[e2e-gate] no tests collected (rc=5) — treating as pass (defensive)" >&2
    fi

    # --- marker write (clean tree only) ------------------------------------
    local head_sha
    head_sha="$(git -C "$repo_root" rev-parse HEAD 2>/dev/null || true)"
    if [[ -z "$head_sha" ]]; then
        echo "[e2e-gate] suite passed but could not resolve HEAD — NO marker written" >&2
        exit 0
    fi

    if [[ -n "$(git -C "$repo_root" status --porcelain 2>/dev/null)" ]]; then
        cat >&2 <<EOF
[e2e-gate] suite passed BUT the working tree is dirty — NO marker written.
The marker must reflect the exact committed content you will push. Commit (or
stash) your changes and re-run the gate so the marker matches HEAD ($head_sha).
EOF
        exit 0
    fi

    local mdir
    mdir="$(marker_dir "$repo_root")" || {
        echo "[e2e-gate] suite passed but could not resolve git dir — NO marker written" >&2
        exit 0
    }
    mkdir -p "$mdir"
    {
        printf 'sha=%s\n' "$head_sha"
        printf 'timestamp=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf 'pytest_rc=%s\n' "$rc"
        printf '%s\n' "$output" | tail -1
    } > "$mdir/$head_sha"
    echo "[e2e-gate] green — wrote marker $mdir/$head_sha (HATS-686)." >&2
    echo "[e2e-gate] 'git push origin master' on this HEAD will now pass instantly." >&2
    exit 0
}

# --- dispatch --------------------------------------------------------------

if [[ "${1:-}" == "--run" ]]; then
    run_mode
else
    check_mode
fi
