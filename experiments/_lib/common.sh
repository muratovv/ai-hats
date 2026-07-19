# shellcheck shell=bash
# Shared helpers for experiment scripts (HATS-1053). Source, don't execute.
# shellcheck disable=SC2034  # SCRUB is consumed by the sourcing scripts

# Ambient session env leaks the parent project into the sandbox: sessions land in
# the wrong project dir, ownership checks read the wrong actor, git plumbing hits
# the real repo (HATS-897 / 944 / 955 / 982 / 886). Prefix every sandbox command.
SCRUB=(env
  -u AI_HATS_DIR -u AI_HATS_PROJECT_DIR
  -u AI_HATS_SESSION_ID -u AI_HATS_ROOT_PID
  -u AI_HATS_VENV
  -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE
)

# Total USD spent by an experiment so far, summed over collected run envelopes.
exp_spent_usd() {
  local files
  files=$(find "$1/runs" -name envelope.json 2>/dev/null)
  if [[ -z "$files" ]]; then
    echo 0
    return
  fi
  echo "$files" | xargs cat | jq -s 'map(.total_cost_usd // 0) | add'
}

# Experiment budget in USD: env override, else the budget.usd file, else empty.
exp_budget_usd() {
  if [[ -n "${AI_HATS_EXP_BUDGET_USD:-}" ]]; then
    echo "$AI_HATS_EXP_BUDGET_USD"
  elif [[ -f "$1/budget.usd" ]]; then
    tr -d '[:space:]' <"$1/budget.usd"
  fi
}
