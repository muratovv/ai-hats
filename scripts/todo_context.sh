#!/usr/bin/env bash
# TODOs an agent should have in context before touching a slice (HATS-1783).
#
# Grouped by the card that retires them, because a TODO without a card is a defect
# (dev_rule_comment_discipline) rather than a plan — those are listed separately.
#
#   scripts/todo_context.sh                        every TODO with a card
#   scripts/todo_context.sh HATS-1785              only that card's
#   scripts/todo_context.sh src/ai_hats/pipeline   only inside that path
set -euo pipefail

cd "$(dirname "$0")/.."

arg="${1:-}"
path="."
card=""
case "$arg" in
"") ;;
HATS-*) card="$arg" ;;
*) path="$arg" ;;
esac

grep_todo() {
	grep -rn --include='*.py' --include='*.sh' --include='*.md' --include='*.yaml' \
		-E 'TODO' "$path" \
		--exclude-dir=.git --exclude-dir=__pycache__ --exclude-dir=.venv \
		--exclude-dir=node_modules 2>/dev/null || true
}

all="$(grep_todo)"

if [ -n "$card" ]; then
	printf '%s\n' "$all" | grep -F "TODO($card)" || echo "no TODO carries $card"
	exit 0
fi

echo "== TODOs with a card =="
carded="$(printf '%s\n' "$all" | grep -E 'TODO\(HATS-[0-9]+\)' || true)"
if [ -z "$carded" ]; then
	echo "  none"
else
	printf '%s\n' "$carded" | grep -oE 'TODO\(HATS-[0-9]+\)' | sort -u | while read -r tag; do
		id="${tag#TODO(}"
		id="${id%)}"
		echo "-- $id"
		printf '%s\n' "$carded" | grep -F "$tag" | sed 's/^/   /'
	done
fi

echo
echo "== TODOs with no card (work, not notes) =="
# Code only: an unowned TODO in source is a defect, while a docs "section not written
# yet" is a different animal. ``.TODO(`` is Go's context idiom, not a marker.
printf '%s\n' "$all" | grep -E '\.(py|sh):' | grep -vE 'TODO\(HATS-[0-9]+\)' |
	grep -vE '\.TODO\(|todo_context\.sh' | sed 's/^/   /' || echo "   none"
