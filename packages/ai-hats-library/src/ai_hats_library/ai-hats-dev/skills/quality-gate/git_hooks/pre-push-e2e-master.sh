#!/usr/bin/env bash
# pre-push: the master push gate, on git's channel. It asks: is the tree that is
# about to reach origin/master green through the full tier.
#
# Check mode (DEFAULT, what git runs): read git's pre-push protocol from stdin
# and, for every local_sha aimed at refs/heads/master, ask the project's own
# `scripts/gates.sh check --rev <sha>` about the stages below. Allow on yes;
# block naming the missing stages and the command that earns them on no. Never
# runs a suite: GitHub closes the push connection ~30s in, so the tier runs OUT
# OF BAND — `scripts/run-e2e-gate.sh` — and the markers are the only evidence.
#
#   git push origin feature   # no master target -> no-op
#   git push origin :master   # deletion -> no-op
#   git push origin master    # allowed only with every stage below marked
#
# Run mode (`--run [--rev <sha>]`): `scripts/gates.sh run` of the stages below.
# `--stages`: print them.
#
# A SEPARATE script from the checks-channel gates on purpose: this one's default
# mode reads git's protocol from stdin, and the check runner gives its children
# stdin=DEVNULL — empty stdin hits the "no master target" fast path and exits 0.
# Reusing it there would produce a gate that is silently always green.
#
# Exit contract, git's: 0 allow, 1 block. Hence `set -uo pipefail` and no `-e`.

set -uo pipefail

zero='0000000000000000000000000000000000000000'

GATE='push-gate'
STAGES='e2e-catalog lint prose-refs ticket-ids env-reference gate-table adr-integrity bidi unit e2e-default e2e-rack e2e-guards e2e-gates e2e-wt e2e-install e2e-surfaces e2e-consent e2e-library e2e-observe'
CHANNEL='githook'
RUN_CMD='scripts/run-e2e-gate.sh'
# A script takes a commit as a flag, not as a make variable. Read by gate.sh.
# shellcheck disable=SC2034
RUN_REV_FMT='--rev %s'

_self_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if ! . "$_self_dir/../lib/gate.sh"; then
    echo "[push-gate] cannot load $_self_dir/../lib/gate.sh — push to master BLOCKED" >&2
    exit 1
fi

check_mode() {
    # Collect master-targeting, non-deletion local_shas from the pre-push
    # protocol. Newline-accumulator instead of an array so the empty case is
    # safe under bash 3.2 + `set -u` (macOS system bash).
    local local_ref local_sha remote_ref _remote_sha
    local need=""
    while read -r local_ref local_sha remote_ref _remote_sha; do
        [[ -z "${local_ref:-}" ]] && continue
        [[ "$remote_ref" != "refs/heads/master" ]] && continue
        [[ "$local_sha" == "$zero" ]] && continue
        need="${need}${local_sha}"$'\n'
    done

    # No master target (feature branch, deletion, empty stdin) -> fast path.
    [[ -z "$need" ]] && exit 0

    local repo_root gates
    repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" || {
        echo "[push-gate] could not resolve the repository root — push to master BLOCKED" >&2
        exit 1
    }
    gates="$repo_root/scripts/gates.sh"
    if [[ ! -f "$gates" ]]; then
        echo "[push-gate] $repo_root has no scripts/gates.sh — nothing could have earned" >&2
        echo "[push-gate] a marker, so the push to master is BLOCKED" >&2
        exit 1
    fi

    local sha tree missing rc
    while IFS= read -r sha; do
        [[ -z "$sha" ]] && continue
        # The subject is CONTENT: a commit that only re-parents an already-marked
        # tree is the same thing the gate already judged.
        tree="$(gate_tree "$repo_root" "$sha")"
        if [[ -z "$tree" ]]; then
            echo "[push-gate] $sha names no tree here — push to master BLOCKED" >&2
            exit 1
        fi
        # shellcheck disable=SC2086
        missing="$(cd "$repo_root" && bash "$gates" check --rev "$sha" $STAGES)"
        rc=$?
        case "$rc" in
            0) continue ;;
            1)
                echo "[push-gate] push to master BLOCKED." >&2
                gate_refusal "$GATE" "$tree" "the master commit you are pushing" \
                             "$RUN_CMD" "$missing" >&2
                echo "To knowingly skip every pre-push hook: git push --no-verify." >&2
                gate_exit "$CHANNEL" refuse
                ;;
            *)
                echo "[push-gate] \`$gates check\` failed with rc=$rc — the gate could not" >&2
                echo "[push-gate] tell, so the push to master is BLOCKED" >&2
                exit 1
                ;;
        esac
    done <<< "$need"

    echo "[push-gate] every required stage is green for the master commit — push allowed" >&2
    gate_exit "$CHANNEL" pass
}

case "${1:-}" in
    --run)
        shift
        gate_run "$GATE" "$STAGES" "$@"
        ;;
    --stages) printf '%s\n' "$STAGES" ;;
    # It requires the whole partition by name, always — nothing here is decided
    # by a diff. Answered anyway, because the renderer asks EVERY gate, and an
    # unknown flag here falls into check mode, which reads git's pre-push
    # protocol from stdin and waits there forever.
    --zones) printf 'none\n' ;;
    *) check_mode ;;
esac
