#!/usr/bin/env bash
# Remove an experiment's tmp sandboxes. Idempotent (HATS-1053).
# Usage: clean.sh <experiment-dir>
set -euo pipefail

[[ $# -eq 1 ]] || { echo "usage: clean.sh <experiment-dir>" >&2; exit 2; }
exp_name=$(basename "$(cd "$1" && pwd)")
base="${AI_HATS_EXP_TMP:-${TMPDIR:-/tmp}/ai-hats-exp}"
rm -rf "${base:?}/${exp_name:?}"
echo "clean: removed $base/$exp_name" >&2
