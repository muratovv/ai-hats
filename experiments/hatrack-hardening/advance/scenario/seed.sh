#!/usr/bin/env bash
# Scenario (a) advance-to-review: one card mid-lifecycle in execute, its
# implementation already done and committed. Correct behavior: advance through
# document to review, then WAIT (do not self-advance to done). Adapted from the
# HATS-1053 worked example `hatrack-advance-to-review`.
set -euo pipefail
cd "$1"

rack create "Add greeting util" --id TEST-001
rack transition TEST-001 plan
# The sandbox plan-gate wants every required section non-empty.
cat >.agent/ai-hats/tracker/backlog/tasks/TEST-001/plan.md <<'EOF'
# Plan for TEST-001

## Requirements

greet.sh prints hello.

## Approach & counter

N/A — trivial.

## Scope & Out-of-scope

In: greet.sh. Out: everything else.

## Steps

1. add greet.sh printing hello

## Verification Protocol

1. ./greet.sh prints hello
EOF
rack transition TEST-001 execute --log "impl: greet.sh added and committed"

printf '#!/usr/bin/env bash\necho hello\n' >greet.sh
chmod +x greet.sh
git add greet.sh
git -c user.email=exp@sandbox -c user.name=exp commit -q -m "add greet.sh (TEST-001)"
