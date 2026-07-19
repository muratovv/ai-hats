#!/usr/bin/env bash
# Prepare one sandbox project for one arm×run of a behavior experiment (HATS-1053).
# Usage: prepare.sh <experiment-dir> <arm> <run-idx>   → prints sandbox path on stdout
set -euo pipefail

# shellcheck source-path=SCRIPTDIR
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

[[ $# -eq 3 ]] || { echo "usage: prepare.sh <experiment-dir> <arm> <run-idx>" >&2; exit 2; }
exp_dir=$(cd "$1" && pwd)
arm=$2
run_idx=$3

arm_dir="$exp_dir/arms/$arm"
scenario_dir="$exp_dir/scenario"
[[ -d "$arm_dir" ]] || { echo "prepare: no such arm dir: $arm_dir" >&2; exit 1; }
[[ -d "$scenario_dir/lib" ]] || { echo "prepare: missing $scenario_dir/lib" >&2; exit 1; }
[[ -x "$scenario_dir/seed.sh" ]] || { echo "prepare: missing executable $scenario_dir/seed.sh" >&2; exit 1; }

base="${AI_HATS_EXP_TMP:-${TMPDIR:-/tmp}/ai-hats-exp}"
sandbox="$base/$(basename "$exp_dir")/$arm/run-$run_idx"

rm -rf "$sandbox"
mkdir -p "$sandbox"

git -C "$sandbox" init -q

# Headless subagents can't answer permission prompts — pre-approve the CLIs under test.
mkdir -p "$sandbox/.claude"
cat >"$sandbox/.claude/settings.json" <<'EOF'
{
  "permissions": {
    "allow": [
      "Bash(rack:*)",
      "Bash(ai-hats:*)",
      "Bash(ai-hats-rack:*)"
    ]
  }
}
EOF

cat >"$sandbox/ai-hats.yaml" <<EOF
schema_version: 4
ai_hats_dir: .agent/ai-hats
provider: claude
library_paths:
  - $scenario_dir/lib
  - $arm_dir
EOF

# Reuse the invoking environment's venv — a per-sandbox pip install per arm×run is waste.
venv="${AI_HATS_EXP_VENV:-${AI_HATS_VENV:-}}"
if [[ -n "$venv" ]]; then
  printf 'venv_path: %s\n' "$venv" >>"$sandbox/ai-hats.yaml"
fi

(cd "$sandbox" && "${SCRUB[@]}" ai-hats self init --no-wizard --no-update -p claude -r exp-agent >&2)

# Commit before seeding: agent sessions and seed transitions run in worktrees of
# this repo — tracked files are what they see, and a worktree needs a commit.
git -C "$sandbox" add -A
git -C "$sandbox" -c user.email=exp@sandbox -c user.name=exp commit -q -m "sandbox seed"

"${SCRUB[@]}" "$scenario_dir/seed.sh" "$sandbox" >&2

echo "$sandbox"
