#!/usr/bin/env bash
# Auto session-retro: policy-aware retro generation.
# Runs as session_end hook — metrics.json is guaranteed to exist (HATS-071).
# Policy/threshold/mode logic lives in Python (ai_hats.retro.auto_retro).
set -euo pipefail

# Fast guards (no Python needed)
ROLE="${AI_HATS_ROLE:-}"
case "$ROLE" in
    judge|test-agent|"") exit 0 ;;
esac
[ -n "${AI_HATS_SESSION_ID:-}" ] || exit 0

# Delegate to Python for policy decision + execution
python3 -m ai_hats.retro.auto_retro || true
