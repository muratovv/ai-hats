#!/usr/bin/env bash
# Scenario (b) review-rework: card in REVIEW with reviewer comments to address.
# Correct behavior: review -> execute (rework) -> document -> review again. The
# review->execute edge is legal (HATS-1052) and fires no worktree merge, so the
# agent loops back and addresses the comments in the same tree.
set -euo pipefail

cd "$1"

rack create "Add greeting util" --id TEST-001
rack transition TEST-001 plan
cat >.agent/ai-hats/tracker/backlog/tasks/TEST-001/plan.md <<'EOF'
# Plan for TEST-001

## Requirements

greet.sh prints a greeting.

## Approach & counter

N/A — trivial.

## Scope & Out-of-scope

In: greet.sh. Out: everything else.

## Steps

1. add greet.sh printing a greeting

## Verification Protocol

1. ./greet.sh prints a greeting
EOF
rack transition TEST-001 execute --log "impl: greet.sh added"

printf '#!/usr/bin/env bash\necho hello\n' >greet.sh
chmod +x greet.sh
git add greet.sh
git -c user.email=exp@sandbox -c user.name=exp commit -q -m "add greet.sh (TEST-001)"

rack transition TEST-001 document
printf '# Summary\n\ngreet.sh added; prints hello.\n' \
  >.agent/ai-hats/tracker/backlog/tasks/TEST-001/summary.md
rack transition TEST-001 review \
  --log "reviewer: greet.sh must accept a NAME argument and print a newline-terminated greeting; please rework"
