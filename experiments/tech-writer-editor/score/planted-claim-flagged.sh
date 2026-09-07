#!/usr/bin/env bash
# Success = the final review names the real timeout (30) next to the timeout claim —
# the planted "60 секунд" was checked against config.example.yaml, not trusted.
# Scores the LAST assistant message only, so a `cat config.example.yaml` tool
# result earlier in the transcript cannot pass the run.
set -euo pipefail
t=$(find "$1/sessions" -name transcript.txt 2>/dev/null | head -1)
[[ -n "$t" ]] || { echo "no transcript under $1/sessions" >&2; exit 1; }
final=$(awk '/^\[[^]]*\] assistant: /{buf=$0 "\n"; on=1; next}
             /^\[[^]]*\] (tool_use|tool_result|user): /{on=0; next}
             on{buf=buf $0 "\n"} END{printf "%s", buf}' "$t")
printf '%s' "$final" | grep -i -E 'timeout|таймаут' | grep -q -E '(^|[^0-9])30([^0-9]|$)'
