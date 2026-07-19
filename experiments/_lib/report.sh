#!/usr/bin/env bash
# Aggregate collected runs into a per-arm report (HATS-1053).
# Usage: report.sh <experiment-dir>   → markdown on stdout
set -euo pipefail

# shellcheck source-path=SCRIPTDIR
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

[[ $# -eq 1 ]] || { echo "usage: report.sh <experiment-dir>" >&2; exit 2; }
exp_dir=$(cd "$1" && pwd)
runs_dir="$exp_dir/runs"
[[ -d "$runs_dir" ]] || { echo "report: no runs under $runs_dir" >&2; exit 1; }

shopt -s nullglob
scores=("$exp_dir/score/"*)

echo "# $(basename "$exp_dir") — report"
echo
echo "| arm | run | status | cost | detail |"
echo "| --- | --- | --- | --- | --- |"

declare -A ok fail timeout crash total cost
arms=()
for arm_path in "$runs_dir"/*/; do
  arm=$(basename "$arm_path")
  arms+=("$arm")
  for run_path in "$arm_path"run-*/; do
    run=$(basename "$run_path")
    total[$arm]=$((${total[$arm]:-0} + 1))
    c=$(jq -r '.total_cost_usd // 0' "$run_path/envelope.json" 2>/dev/null || echo 0)
    cost[$arm]=$(awk -v a="${cost[$arm]:-0}" -v b="$c" 'BEGIN { printf "%.4f", a + b }')
    c=$(awk -v x="$c" 'BEGIN { printf "%.2f", x }')
    code=$(jq -r '.exit_code' "$run_path/status.json")
    timed=$(jq -r '.timed_out' "$run_path/status.json")
    if [[ "$timed" == true ]]; then
      timeout[$arm]=$((${timeout[$arm]:-0} + 1))
      echo "| $arm | $run | timeout | \$$c | agent exceeded cap |"
      continue
    fi
    if [[ "$code" != 0 ]]; then
      crash[$arm]=$((${crash[$arm]:-0} + 1))
      echo "| $arm | $run | crash | \$$c | exit=$code |"
      continue
    fi
    verdict=success
    detail=ok
    for s in "${scores[@]}"; do
      [[ -x "$s" ]] || continue
      if ! "$s" "$run_path" >/dev/null 2>&1; then
        verdict=fail
        detail="$(basename "$s")"
        break
      fi
    done
    if [[ "$verdict" == success ]]; then
      ok[$arm]=$((${ok[$arm]:-0} + 1))
    else
      fail[$arm]=$((${fail[$arm]:-0} + 1))
    fi
    echo "| $arm | $run | $verdict | \$$c | $detail |"
  done
done

echo
echo "| arm | runs | success | fail | timeout | crash | rate | cost |"
echo "| --- | --- | --- | --- | --- | --- | --- | --- |"
for arm in "${arms[@]}"; do
  t=${total[$arm]:-0}
  s=${ok[$arm]:-0}
  rate=0
  [[ $t -gt 0 ]] && rate=$((s * 100 / t))
  arm_cost=$(awk -v x="${cost[$arm]:-0}" 'BEGIN { printf "%.2f", x }')
  echo "| $arm | $t | $s | ${fail[$arm]:-0} | ${timeout[$arm]:-0} | ${crash[$arm]:-0} | ${rate}% | \$$arm_cost |"
done

echo
spent=$(exp_spent_usd "$exp_dir")
spent=$(awk -v x="$spent" 'BEGIN { printf "%.2f", x }')
budget=$(exp_budget_usd "$exp_dir")
if [[ -n "$budget" ]]; then
  echo "Spent: \$$spent of \$$budget budget."
else
  echo "Spent: \$$spent (no budget set)."
fi
