#!/usr/bin/env bash
# Secondary mechanical signal — tool_use from the provider JSONL: the review
# transition must be driven by a real `rack` CLI call, not hallucinated and not
# the Skill tool used as an executor (the haiku noise class, HATS-1053).
# Collected JSONL lives under <run-dir>/provider-jsonl/.
set -uo pipefail
run="$1"
shopt -s nullglob
jsonl=("$run"/provider-jsonl/*.jsonl)
if [[ ${#jsonl[@]} -eq 0 ]]; then
  echo "drove-review-via-rack: no provider JSONL collected" >&2
  exit 1
fi
# A rack transition command whose line also names the review target.
if grep -hE 'rack[^\n]*transition' "${jsonl[@]}" | grep -qE 'review'; then
  exit 0
fi
echo "drove-review-via-rack: no 'rack transition … review' found in provider JSONL" >&2
exit 1
