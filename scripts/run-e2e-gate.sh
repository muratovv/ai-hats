#!/usr/bin/env bash
# HATS-686 — ergonomic entry point for the out-of-band maintainer e2e+smoke gate.
#
# Runs every stage of the `push-gate` composition (`scripts/ci-local.sh
# --stages push-gate`), the e2e tier among them, and on green + a clean working
# tree writes a pass-marker keyed to HEAD's TREE. Once the marker exists, `git push origin
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

# HATS-1337: gates are no longer copied into `.githooks/pre-push.d/` — they run
# in place from the library, so resolve the library and read the gate from there.
# Deliberately NOT through the hook entry point: that one runs the whole chain.
py="${PYTHON:-python3}"
[[ -x "$repo_root/.venv/bin/python3" ]] && py="$repo_root/.venv/bin/python3"
libroot="$("$py" -c 'import ai_hats_library, pathlib; print(pathlib.Path(ai_hats_library.__file__).parent)' 2>/dev/null || true)"
hook="${libroot}/ai-hats-dev/skills/maintainer-quality-gate/git_hooks/pre-push-e2e-master.sh"

if [[ -z "$libroot" || ! -f "$hook" ]]; then
    echo "[run-e2e-gate] cannot resolve the e2e-master gate in the ai-hats library." >&2
    echo "[run-e2e-gate] install ai-hats into this checkout first: ai-hats self init" >&2
    exit 1
fi

exec bash "$hook" --run "$@"
