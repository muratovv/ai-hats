#!/usr/bin/env bash
# Scenario (b) review-rework: card in REVIEW with reviewer comments to address.
# Correct behavior ONCE HATS-1052 lands: review -> execute (rework) -> document
# -> review again.
#
# NON-RUNNABLE until HATS-1052 (the review->execute edge) exists: the agent's
# required first move is refused by the FSM today, so no arm can succeed. The
# guard below fails fast in `prepare` so no paid agent run is wasted. Set
# HATS_1052_LANDED=1 to force once the edge lands. See ../NONRUNNABLE.md.
set -euo pipefail

if [[ "${HATS_1052_LANDED:-}" != "1" ]]; then
  echo "rework: NON-RUNNABLE until HATS-1052 (review->execute edge). Set HATS_1052_LANDED=1 to force." >&2
  exit 1
fi

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
