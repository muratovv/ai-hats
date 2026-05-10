#!/usr/bin/env bash
# Auto session-retro: policy-aware retro generation.
# Runs as session_end hook — metrics.json is guaranteed to exist (HATS-071).
# Policy/threshold/mode logic lives in Python (ai_hats.retro.auto_retro).
set -euo pipefail

# Fast guards (no Python needed)
ROLE="${AI_HATS_ROLE:-}"
case "$ROLE" in
    test-agent|"") exit 0 ;;
esac
[ -n "${AI_HATS_SESSION_ID:-}" ] || exit 0

# Recursion guard (HATS-252): the session-reviewer sub-process sets
# HATS_SKIP_RETRO=1 in its env so the sub-Claude session it spawns does not
# re-fire this hook and re-spawn another reviewer ad infinitum. Leave a
# breadcrumb so the loop is debuggable if it ever fires from the wrong place.
if [ "${HATS_SKIP_RETRO:-0}" = "1" ]; then
    log_dir=".gitlog/session_${AI_HATS_SESSION_ID}"
    mkdir -p "$log_dir" 2>/dev/null || true
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '%s\thook\tskip\trecursion-guard\n' "$ts" \
        >> "$log_dir/retro.log" 2>/dev/null || true
    exit 0
fi

# Delegate to Python for policy decision + execution.
# Use the interpreter next to the ai-hats binary (same venv) — bare
# python3 from PATH may resolve to a system Python without ai_hats.
AH="$(command -v ai-hats 2>/dev/null || true)"
if [ -n "$AH" ]; then
    "$(dirname "$AH")/python3" -m ai_hats.retro.auto_retro || true
fi
