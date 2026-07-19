#!/usr/bin/env bash
# Collect one run's raw material: session dir, provider JSONL, final backlog state.
# Usage: collect.sh <sandbox> <dest-run-dir>
set -euo pipefail

[[ $# -eq 2 || $# -eq 3 ]] || { echo "usage: collect.sh <sandbox> <dest-run-dir> [arm-dir]" >&2; exit 2; }
sandbox=$(cd "$1" && pwd)
dest=$(cd "$2" && pwd)
arm_dir=${3:-}

# Arm identity proof: the composition snapshot carries no source paths (labels
# library entries "built-in"), so record the config that wired the arm in plus
# a content hash of the arm dir — score scripts diff these across arms.
cp "$sandbox/ai-hats.yaml" "$dest/ai-hats.yaml"
if [[ -n "$arm_dir" && -d "$arm_dir" ]]; then
  (cd "$arm_dir" && find . -type f -print0 | sort -z | xargs -0 shasum -a 256) >"$dest/arm-manifest.txt"
fi

# metrics.json in here carries the composition snapshot — the proof of arm identity.
runs_root="$sandbox/.agent/ai-hats/sessions/runs"
if [[ -d "$runs_root" ]]; then
  cp -R "$runs_root" "$dest/sessions"
else
  echo "collect: no session runs under $runs_root (fail-soft)" >&2
fi

# Final backlog state — the primary mechanical-scoring signal (fs-as-truth).
tasks_root="$sandbox/.agent/ai-hats/tracker/backlog/tasks"
if [[ -d "$tasks_root" ]]; then
  cp -R "$tasks_root" "$dest/backlog"
else
  echo "collect: no backlog under $tasks_root (fail-soft)" >&2
fi

# Provider JSONL (tool-call SSOT): locate by claude_session_id — the project-key
# munging depends on the agent worktree's realpath, filename search is robust.
jsonl_found=false
for m in "$sandbox"/.agent/ai-hats/sessions/runs/session_*/metrics.json; do
  [[ -f "$m" ]] || continue
  cid=$(jq -r '.claude_session_id // empty' "$m")
  [[ -n "$cid" ]] || continue
  f=$(find "$HOME/.claude/projects" -name "$cid.jsonl" 2>/dev/null | head -1)
  if [[ -n "$f" ]]; then
    mkdir -p "$dest/provider-jsonl"
    cp "$f" "$dest/provider-jsonl/"
    jsonl_found=true
  fi
done
[[ "$jsonl_found" == true ]] || echo "collect: no provider JSONL found (fail-soft)" >&2
