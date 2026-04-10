#!/usr/bin/env bash
# Auto session-retro: generate programmatic retro for productive work sessions.
# Runs as session_end hook — metrics.json is guaranteed to exist (HATS-071).
set -euo pipefail

# Skip non-work sessions (judge, sub-agent, test-agent, etc.)
ROLE="${AI_HATS_ROLE:-}"
case "$ROLE" in
    judge|test-agent|"") exit 0 ;;
esac

# Read metrics
SESSION_ID="${AI_HATS_SESSION_ID:-}"
[ -n "$SESSION_ID" ] || exit 0

METRICS=".gitlog/session_${SESSION_ID}/metrics.json"
[ -f "$METRICS" ] || exit 0

# Check productivity: turns >= threshold
MIN_TURNS="${AI_HATS_AUTO_RETRO_MIN_TURNS:-5}"
TURNS=$(python3 -c "import json; print(json.load(open('$METRICS')).get('turns', 0))" 2>/dev/null || echo 0)

if [ "$TURNS" -lt "$MIN_TURNS" ]; then
    exit 0
fi

# Generate programmatic retro (fast, no LLM, no cost)
ai-hats retro "$SESSION_ID" --mode programmatic >/dev/null 2>&1 || true
