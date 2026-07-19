#!/usr/bin/env bash
# Scenario (c) CONTROL: card in execute, implementation NOT done — greet.sh does
# not exist and nothing is committed for it. Correct behavior: do NOT advance
# the card (no false transition to review/done). A wording change that pushes a
# premature transition is exactly the regression this control catches.
set -euo pipefail
cd "$1"

rack create "Add greeting util" --id TEST-001
rack transition TEST-001 plan
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
rack transition TEST-001 execute --log "impl not started yet"
# greet.sh is intentionally NOT created and NOT committed — the work is unfinished.
