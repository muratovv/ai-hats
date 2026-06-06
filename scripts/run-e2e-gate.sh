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
hook="$repo_root/.githooks/pre-push.d/maintainer-quality-gate-pre-push-e2e-master.sh"

if [[ ! -x "$hook" ]]; then
    echo "[run-e2e-gate] gate hook not installed at:" >&2
    echo "  $hook" >&2
    echo "[run-e2e-gate] compose the maintainer role first: ai-hats self init" >&2
    exit 1
fi

exec bash "$hook" --run "$@"
