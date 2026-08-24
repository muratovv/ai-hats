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

# An id is ``<prefix>-<digit>`` and then the author's own tail — ai-hats-rack routes
# ``HATS-120b`` like ``HATS-1249`` (ai_hats_rack/ids.py) — and one marker may name
# several cards, in which case it is every one of their work.
card_re='HATS-[0-9][A-Za-z0-9-]*'
in_todo='TODO\([^)]*'         # anywhere inside the marker's parens
ends='([^A-Za-z0-9-]|$)'      # HATS-120 does not match inside HATS-120b

if [ -n "$card" ]; then
	printf '%s\n' "$all" | grep -E "$in_todo$card$ends" || echo "no TODO carries $card"
	exit 0
fi

echo "== TODOs with a card =="
carded="$(printf '%s\n' "$all" | grep -E "$in_todo$card_re" || true)"
if [ -z "$carded" ]; then
	echo "  none"
else
	printf '%s\n' "$carded" | grep -oE "$in_todo" | grep -oE "$card_re" |
		sort -u | while read -r id; do
		echo "-- $id"
		printf '%s\n' "$carded" | grep -E "$in_todo$id$ends" | sed 's/^/   /'
	done
fi

echo
echo "== TODOs with no card (work, not notes) =="
# Code only: an unowned TODO in source is a defect, while a docs "section not written
# yet" is a different animal. ``.TODO(`` is Go's context idiom, not a marker.
printf '%s\n' "$all" | grep -E '\.(py|sh):' | grep -vE "$in_todo$card_re" |
	grep -vE '\.TODO\(|todo_context\.sh' | sed 's/^/   /' || echo "   none"
