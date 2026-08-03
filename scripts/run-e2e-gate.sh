#!/usr/bin/env bash
# HATS-686 — ergonomic entry point for the out-of-band maintainer e2e+smoke gate.
#
# Runs the full `pytest -m "(integration or smoke) and not quarantine"
# tests/e2e/ tests/smoke/` suite and, on green + a clean working tree, writes a
# pass-marker keyed to HEAD's SHA. Once the marker exists, `git push origin
# master` passes the pre-push gate INSTANTLY (no suite inside the doomed SSH
# connection window — see HATS-686 / HATS-684).
#
# Thin delegator: ALL gate logic lives in the installed pre-push hook's
# `--run` mode, so there is one source of truth.
#
# Usage:
#   scripts/run-e2e-gate.sh
# then, once it reports a written marker:
#   git push origin master
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"

# HATS-1337: gates are no longer copied to `.githooks/pre-push.d/` — ask the
# composition where this one lives, the same way the dispatcher does.
hook=""
while IFS=$'\t' read -r kind path; do
    if [[ "$kind" == "gate" && "$path" == *"pre-push-e2e-master.sh" ]]; then
        hook="$path"
    fi
done < <(ai-hats githooks resolve pre-push --project-dir "$repo_root" 2>/dev/null || true)

if [[ -z "$hook" || ! -f "$hook" ]]; then
    echo "[run-e2e-gate] the e2e-master gate is not in this project's composition." >&2
    echo "[run-e2e-gate] compose the maintainer role first: ai-hats self init" >&2
    exit 1
fi

exec bash "$hook" --run "$@"
