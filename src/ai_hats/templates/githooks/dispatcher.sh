#!/usr/bin/env bash
# AI-HATS-DISPATCHER-MARKER v1
# Managed by ai-hats. Do not edit manually.
# Add hooks by declaring `git_hooks:` in a skill's metadata.yaml — they
# will be installed into .githooks/<event>.d/ on the next composition.
set -uo pipefail

EVENT="$(basename "$0")"
EVENT_D="$(dirname "$0")/${EVENT}.d"

if [[ ! -d "$EVENT_D" ]]; then
    exit 0
fi

# Run scripts in lexicographic order. First non-zero exit aborts the chain
# (matches git's expectation that a failed pre-commit blocks the commit).
shopt -s nullglob
for script in "$EVENT_D"/*; do
    [[ -f "$script" && -x "$script" ]] || continue
    "$script" "$@"
    rc=$?
    if [[ $rc -ne 0 ]]; then
        echo "ai-hats: hook '$(basename "$script")' failed (exit $rc)" >&2
        exit "$rc"
    fi
done

exit 0
