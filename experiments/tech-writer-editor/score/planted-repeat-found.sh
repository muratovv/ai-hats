#!/usr/bin/env bash
# Success = the final review quotes the second telling of the one repeated idea
# ("портят друг другу сборку") and calls it a repetition.
# Scores the LAST assistant message only (same extraction as planted-claim-flagged.sh).
set -euo pipefail
t=$(find "$1/sessions" -name transcript.txt 2>/dev/null | head -1)
[[ -n "$t" ]] || { echo "no transcript under $1/sessions" >&2; exit 1; }
final=$(awk '/^\[[^]]*\] assistant: /{buf=$0 "\n"; on=1; next}
             /^\[[^]]*\] (tool_use|tool_result|user): /{on=0; next}
             on{buf=buf $0 "\n"} END{printf "%s", buf}' "$t")
printf '%s' "$final" | grep -q -i 'портят друг другу сборку'
printf '%s' "$final" | grep -q -i -E 'повтор|дубл|repeat|duplicat|twice|same (idea|point|thing)|redundan|уже (сказано|было|говорил)'
