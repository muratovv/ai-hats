#!/usr/bin/env bash
# Sweep stale ai-hats test cruft out of the temp root (HATS-570).
#
# Two leak sources accumulate over many test runs and (on a loaded host)
# slow APFS metadata ops enough to time out venv-tier `pip install`:
#   * ai-hats-wt-*    — worktree dirs born by tempfile.mkdtemp in
#                       worktree.py when a test forgets to clean up.
#   * pytest-of-*     — pytest tmp_path runs (heavy venv-tier sandboxes).
#
# Idempotent. DRY-RUN by default — prints what it WOULD remove and exits
# without touching anything. Pass --force to actually `rm -rf`.
#
# Usage:
#   bash scripts/clean-tmp-cruft.sh            # dry-run (safe preview)
#   bash scripts/clean-tmp-cruft.sh --force    # actually delete
#
# Scans ${TMPDIR:-/tmp} and /tmp (deduplicated). Never deletes the
# directory the caller is standing in (or any ancestor of it), so it is
# safe to run from inside a live ai-hats worktree.
set -euo pipefail

FORCE=0
for arg in "$@"; do
    case "$arg" in
        --force) FORCE=1 ;;
        -h|--help)
            sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *)
            printf 'unknown argument: %s (use --force or --help)\n' "$arg" >&2
            exit 2 ;;
    esac
done

if [[ -t 1 ]]; then
    BOLD="\033[1m"; DIM="\033[2m"; GREEN="\033[32m"; YELLOW="\033[33m"; RESET="\033[0m"
else
    BOLD=""; DIM=""; GREEN=""; YELLOW=""; RESET=""
fi

PATTERNS=("ai-hats-wt-*" "pytest-of-*")

# Build a deduplicated list of roots to scan.
ROOTS=()
for r in "${TMPDIR:-/tmp}" "/tmp"; do
    r="${r%/}"   # strip trailing slash
    [[ -d "$r" ]] || continue
    skip=0
    for seen in "${ROOTS[@]:-}"; do
        [[ "$seen" == "$r" ]] && { skip=1; break; }
    done
    [[ "$skip" -eq 0 ]] && ROOTS+=("$r")
done

PWD_REAL="$(cd "$PWD" && pwd -P)"

# True when $1 is the cwd or an ancestor of it — never delete those.
is_in_use() {
    case "$PWD_REAL/" in
        "$1"/*) return 0 ;;
    esac
    return 1
}

total=0
freed_kb=0
for root in "${ROOTS[@]}"; do
    for pat in "${PATTERNS[@]}"; do
        for path in "$root"/$pat; do
            [[ -e "$path" ]] || continue          # glob did not match
            real="$(cd "$path" 2>/dev/null && pwd -P || echo "$path")"
            if is_in_use "$real"; then
                printf "  ${YELLOW}skip${RESET} %s ${DIM}(in use — cwd is inside)${RESET}\n" "$path"
                continue
            fi
            sz_kb="$(du -sk "$path" 2>/dev/null | cut -f1 || echo 0)"
            total=$((total + 1))
            freed_kb=$((freed_kb + sz_kb))
            if [[ "$FORCE" -eq 1 ]]; then
                rm -rf "$path"
                printf "  ${GREEN}rm${RESET}   %s\n" "$path"
            else
                printf "  ${DIM}would rm${RESET} %s\n" "$path"
            fi
        done
    done
done

freed_mb=$((freed_kb / 1024))
if [[ "$total" -eq 0 ]]; then
    printf "${GREEN}nothing to clean${RESET} (roots: %s)\n" "${ROOTS[*]}"
elif [[ "$FORCE" -eq 1 ]]; then
    printf "${BOLD}removed %d dir(s), ~%d MB freed${RESET}\n" "$total" "$freed_mb"
else
    printf "${BOLD}DRY-RUN${RESET}: %d dir(s), ~%d MB would be freed. Re-run with ${BOLD}--force${RESET} to delete.\n" "$total" "$freed_mb"
fi
