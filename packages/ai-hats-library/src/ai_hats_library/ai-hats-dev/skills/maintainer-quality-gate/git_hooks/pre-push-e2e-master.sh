#!/usr/bin/env bash
# HATS-550 / HATS-686 — pre-push e2e+smoke gate for pushes that include
# refs/heads/master, DECOUPLED from the push connection (HATS-686).
#
# Two modes (selected by argv):
#
#   1. CHECK MODE (default — git invokes the hook with the standard pre-push
#      protocol on stdin: `<local_ref> <local_sha> <remote_ref> <remote_sha>`).
#      INSTANT: for every line targeting refs/heads/master with a non-zero
#      local_sha, require a green pass-marker keyed to the TREE that sha names.
#      All present → allow (exit 0). Any missing → block (exit 1) with the run
#      command. No pytest, no network → finishes well under GitHub's ~30s SSH
#      idle window, which killed the old in-hook run (HATS-684 — exit
#      141, twice).
#
#   2. RUN MODE (`--run`, invoked manually / via scripts/run-e2e-gate.sh).
#      Runs the stages `scripts/ci-local.sh` names for the `push-gate`
#      composition — the e2e tier plus the cheap ones that must be green with it
#      (HATS-726) — from the repo root. On pass AND a clean working tree, writes
#      a marker keyed to `git rev-parse HEAD^{tree}` recording those stages. A
#      DIRTY tree runs the suite but writes NO marker: the gate builds wheels
#      from the working tree, so the marker is only honest when clean-tree ==
#      HEAD content == the content that will be pushed.
#
# Marker store: <git-common-dir>/ai-hats/e2e-gate/<tree> (under .git/, never
# committed, shared across worktrees). Keyed by CONTENT since HATS-1601: a
# commit that only re-parents an already-marked tree is the same thing the gate
# already judged. Markers from the pre-1601 scheme are named by commit sha and
# record `sha=` rather than `tree=`, so `gate_marker_covers` never reads them
# and the 30-day sweep drops them in its own time — inert, not migrated.
# Forging a marker (`touch …`) is the moral equivalent of `git push
# --no-verify` — a deliberate local act by the trusted maintainer, NOT an
# accidental normal-flow bypass. See SKILL.md.
#
# HATS-1137: the marker mechanism moved to ../lib/gate-marker.sh, parameterised
# by gate name. This gate keeps the name `e2e-gate`, so the directory it reads
# and writes is byte-identical to the pre-1137 one — that move cost the
# maintainer no marker, and no push turned into a surprise full-suite run.
#
# Run-mode behaviour carried over from earlier tickets:
#   * HATS-568: sweeps stale `build/` wheel artefacts before the run.
#   * HATS-731: previews stale tmp cruft (`ai-hats-wt-*`, `pytest-of-*`) via
#     scripts/clean-tmp-cruft.sh before the run — the leak source that sweeper
#     (HATS-570) was built for. DRY-RUN by default (it matches every
#     `ai-hats-wt-*` by name and cannot tell a leaked test worktree from a live
#     session, so it never auto-deletes); opt in to real `--force` deletion
#     with AI_HATS_E2E_CLEAN_TMP=1. Guarded on the script's presence (a no-op
#     in consuming projects that ship the hook but not scripts/).
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

# The gate's name IS its marker directory. Renaming it invalidates every marker
# on every maintainer's disk at once, so it does not change.
GATE_NAME='e2e-gate'
#: The composition the project names for this gate — asked, never stated here.
COMPOSITION='push-gate'
CHANNEL='githook'
RUN_CMD='scripts/run-e2e-gate.sh'

# --- shared helpers --------------------------------------------------------

# HATS-1337 runs gates in place from the library rather than copying them into
# `.githooks/`, so the sibling lib is reachable from $0's own directory.
_self_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if ! . "$_self_dir/../lib/gate-marker.sh" || ! . "$_self_dir/../lib/gate.sh"; then
    echo "[e2e-gate] cannot load $_self_dir/../lib/ — push to master BLOCKED" >&2
    exit 1
fi

# --- check mode (default) --------------------------------------------------

check_mode() {
    # Collect master-targeting, non-deletion local_shas from the pre-push
    # protocol on stdin. Newline-accumulator instead of an array so the
    # empty case is safe under bash 3.2 + `set -u` (macOS system bash).
    local local_ref local_sha remote_ref _remote_sha
    local need=""
    # The fourth field is read because the protocol sends four per line, not
    # because anything here wants it.
    while read -r local_ref local_sha remote_ref _remote_sha; do
        [[ -z "${local_ref:-}" ]] && continue
        [[ "$remote_ref" != "refs/heads/master" ]] && continue
        [[ "$local_sha" == "$zero" ]] && continue
        need="${need}${local_sha}"$'\n'
    done

    # No master target (feature branch, deletion, empty stdin) → fast-path.
    if [[ -z "$need" ]]; then
        exit 0
    fi

    gate_marker_dir "$GATE_NAME" "." >/dev/null || {
        echo "[e2e-gate] could not resolve git dir — push to master BLOCKED" >&2
        exit 1
    }

    local stages
    stages="$(gate_stages "$(git rev-parse --show-toplevel)/scripts/ci-local.sh" "$COMPOSITION")"

    local sha tree unmarked=''
    while IFS= read -r sha; do
        [[ -z "$sha" ]] && continue
        # The subject is CONTENT: a commit that only re-parents an already-marked
        # tree is the same thing the gate already judged (HATS-1601).
        tree="$(gate_tree "." "$sha")"
        if [[ -z "$tree" ]] || ! gate_marker_ok "." "$tree" $stages; then
            unmarked="${tree:-$sha}"
            break
        fi
    done <<< "$need"

    if [[ -z "$unmarked" ]]; then
        echo "[e2e-gate] green e2e marker present for the master HEAD — push allowed (HATS-686)" >&2
        gate_exit "$CHANNEL" pass
    fi

    # The suite runs OUT OF BAND (HATS-686): GitHub closes the push SSH
    # connection ~30s in, so a suite taking minutes cannot run inside pre-push.
    echo "[e2e-gate] push to master BLOCKED." >&2
    gate_refusal "$GATE_NAME" "$unmarked" "the master commit you are pushing" \
                 "$RUN_CMD" "$stages" >&2
    echo "To knowingly skip every pre-push hook: git push --no-verify." >&2
    gate_exit "$CHANNEL" refuse
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

    # HATS-726: the marker has to mean "everything CI checks is green". Before
    # the cheap stages joined the composition the gate ran only the e2e tier, so
    # a ruff error passed the most expensive gate in the repo and turned master
    # red minutes later.
    local dispatcher="$repo_root/scripts/ci-local.sh" stages
    stages="$(gate_stages "$dispatcher" "$COMPOSITION")"
    if [[ -z "$stages" ]]; then
        echo "[e2e-gate] $dispatcher names no $COMPOSITION composition — gate ABORTED" >&2
        exit 1
    fi

    echo "[e2e-gate] running $stages out of band (HATS-686, no bypass)" >&2

    # HATS-568: clean stale wheel-build artefacts (worktree-tier e2e tests
    # `pip install` against the repo and write to build/; a leftover dist-info
    # dir causes "File exists" failures on the next run). Idempotent rm.
    if [[ -d "$repo_root/build" ]]; then
        echo "[e2e-gate] cleaning stale $repo_root/build/ (HATS-568)" >&2
        rm -rf "$repo_root/build" 2>/dev/null || true
    fi

    # HATS-731: clear stale ai-hats test cruft (ai-hats-wt-*, pytest run dirs)
    # from TMPDIR before the heavy run — the leak source scripts/clean-tmp-cruft.sh
    # (HATS-570) was built to clear, wired into the gate it speeds up.
    # The sweep DELETES by default (HATS-1624). It was a preview for as long as
    # the sweeper judged by name alone; now it deletes only on proof of death —
    # a worktree git no longer tracks, a run dir whose .lock names an exited pid
    # — so a live sibling worktree or a concurrent run is never a candidate.
    # AI_HATS_E2E_CLEAN_TMP=1 escalates to --force: also the unlocked run dirs
    # pytest's own rotation keeps for triage.
    # Guarded on presence so it is a no-op anywhere the script is absent
    # (consumers ship the hook, not scripts/).
    local sweep="$repo_root/scripts/clean-tmp-cruft.sh"
    if [[ -x "$sweep" ]]; then
        if [[ "${AI_HATS_E2E_CLEAN_TMP:-0}" == "1" ]]; then
            echo "[e2e-gate] sweeping stale tmp cruft --force (HATS-731/HATS-1624)" >&2
            bash "$sweep" --force >&2 || true
        else
            echo "[e2e-gate] reaping provably-dead tmp cruft (HATS-1624):" >&2
            bash "$sweep" >&2 || true
        fi
    fi

    gate_export_pytest_addopts "$GATE_NAME" pytest

    # HATS-645: arm the tier-2 venv fixture's fail-closed mode.
    export AI_HATS_E2E_REQUIRE_VENV=1

    local log rc
    # Not `mktemp -t e2e-gate`: BSD appends its own X's, GNU refuses the
    # template outright ("too few X's"), and the failure is silent — `log` goes
    # empty and the explanation below is skipped on every Linux host.
    log="$(mktemp "${TMPDIR:-/tmp}/e2e-gate.XXXXXX")" || log=''
    gate_run "$dispatcher" "$COMPOSITION" "$log"
    rc=$?

    # rc 5 is pytest's "nothing collected". Defensive since HATS-550: a renamed
    # marker or an empty folder must not permanently brick `git push master`.
    if [[ $rc -ne 0 && $rc -ne 5 ]]; then
        if [[ -n "$log" ]] && grep -q "AI_HATS_E2E_REQUIRE_VENV" "$log" 2>/dev/null; then
            cat >&2 <<'EOF'

[e2e-gate] The failure above is a FAIL-CLOSED venv-tier skip (HATS-645): the
tier-2 e2e venv could not be built (offline / cold pip cache), so the gate
could not actually run those tests. Restore network / warm the pip cache and
re-run the gate.
EOF
        fi
        rm -f "$log" 2>/dev/null || true
        cat >&2 <<'EOF'

Fix the failing tests, then re-run the gate. (To push without any gate:
git push --no-verify — disables every pre-push hook too.)
EOF
        exit 1
    fi
    [[ $rc -eq 5 ]] && echo "[e2e-gate] nothing collected (rc=5) — treating as pass (defensive)" >&2
    rm -f "$log" 2>/dev/null || true

    # --- marker write (clean tree only) ------------------------------------
    local tree
    tree="$(gate_tree "$repo_root" HEAD)"
    if [[ -z "$tree" ]]; then
        echo "[e2e-gate] suite passed but HEAD's tree does not resolve — NO marker written" >&2
        exit 0
    fi

    gate_stamp "$GATE_NAME" "$repo_root" "$tree" "$stages" "pytest_rc=$rc" || exit 0
    echo "[e2e-gate] 'git push origin master' on this content will now pass instantly." >&2
    exit 0
}

# --- dispatch --------------------------------------------------------------

if [[ "${1:-}" == "--run" ]]; then
    run_mode
else
    check_mode
fi
