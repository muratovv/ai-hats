#!/usr/bin/env bash
# Run N sessions of one arm and collect each into <experiment>/runs/ (HATS-1053).
# Usage: run.sh <experiment-dir> <arm> <n-runs> <model>
set -euo pipefail

[[ $# -eq 4 ]] || { echo "usage: run.sh <experiment-dir> <arm> <n-runs> <model>" >&2; exit 2; }
exp_dir=$(cd "$1" && pwd)
arm=$2
n=$3
model=$4

lib_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source-path=SCRIPTDIR
source "$lib_dir/common.sh"
exp_name=$(basename "$exp_dir")
task=$(<"$exp_dir/scenario/task.txt")
run_timeout="${AI_HATS_EXP_RUN_TIMEOUT:-600}"

for ((i = 1; i <= n; i++)); do
  echo "run $arm/$i: prepare" >&2
  sandbox=$("$lib_dir/prepare.sh" "$exp_dir" "$arm" "$i")
  dest="$exp_dir/runs/$arm/run-$i"
  rm -rf "$dest"
  mkdir -p "$dest"

  echo "run $arm/$i: agent ($model, ${run_timeout}s cap)" >&2
  start=$SECONDS
  set +e
  (cd "$sandbox" && "${SCRUB[@]}" timeout "$run_timeout" ai-hats agent exp-agent \
    --task "$task" --model "$model" \
    --tag "exp=$exp_name" --tag "arm=$arm" --tag "run=$i" \
    --json) >"$dest/envelope.json" 2>"$dest/agent.stderr.log"
  code=$?
  set -e

  timed_out=false
  [[ $code -eq 124 ]] && timed_out=true
  printf '{"exit_code": %d, "duration_s": %d, "timed_out": %s}\n' \
    "$code" "$((SECONDS - start))" "$timed_out" >"$dest/status.json"

  "$lib_dir/collect.sh" "$sandbox" "$dest" "$exp_dir/arms/$arm" >&2
  echo "run $arm/$i: exit=$code" >&2
done
