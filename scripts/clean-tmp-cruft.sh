#!/usr/bin/env bash
# Sweep stale ai-hats test cruft out of the temp root (HATS-570, HATS-1624).
#
# Two leak sources accumulate over many test runs and (on a loaded host)
# slow APFS metadata ops enough to time out venv-tier `pip install`:
#   * ai-hats-wt-*           — worktree dirs born by tempfile.mkdtemp in
#                              worktree.py when a test forgets to clean up.
#   * pytest-of-*/pytest-<n> — ONE pytest run's tmp_path tree. Never the
#                              pytest-of-* root: it holds the live runs too.
#
# Deletes only what it can PROVE is dead, so it is safe to run at any time,
# including while other test runs and agent sessions are working:
#   * a worktree dir git no longer tracks;
#   * a run dir whose .lock names a pid that has exited.
# Every other answer — a live owner, an unreadable lock, an unusable `ps` —
# is not proof, and the dir is kept. pytest's own rotation keeps the last 3
# unlocked run dirs for triage; those are left to it unless --force.
#
# Usage:
#   bash scripts/clean-tmp-cruft.sh            # reap the provably dead
#   bash scripts/clean-tmp-cruft.sh --dry-run  # preview, touch nothing
#   bash scripts/clean-tmp-cruft.sh --force    # also take unlocked run dirs
#
# Scans ${TMPDIR:-/tmp} and /tmp (deduplicated). Never deletes the directory
# the caller is standing in (or an ancestor), so it is safe from inside a
# live ai-hats worktree.
set -euo pipefail

FORCE=0
DRY=0
for arg in "$@"; do
    case "$arg" in
        --force) FORCE=1 ;;
        --dry-run|-n) DRY=1 ;;
        -h|--help)
            sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *)
            printf 'unknown argument: %s (use --dry-run, --force or --help)\n' "$arg" >&2
            exit 2 ;;
    esac
done

if [[ -t 1 ]]; then
    BOLD="\033[1m"; DIM="\033[2m"; GREEN="\033[32m"; YELLOW="\033[33m"; RESET="\033[0m"
else
    BOLD=""; DIM=""; GREEN=""; YELLOW=""; RESET=""
fi

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

# True when $1 is a worktree its repo still tracks — a developer's open work,
# not leak. The name alone cannot tell the two apart, and there were 13 live
# ones matching the glob when this was written (HATS-1624). An admin dir that
# no longer exists means the shell was pruned; that one IS leak.
is_registered_worktree() {
    [[ -e "$1/.git" ]] || return 1                # no .git ⇒ not a worktree
    command -v git >/dev/null 2>&1 || return 0    # unknowable ⇒ never proof ⇒ keep
    local gitdir
    gitdir="$(git -C "$1" rev-parse --absolute-git-dir 2>/dev/null)" || return 1
    [[ -d "$gitdir" ]]
}

# True when $1 is provably not a running process. `ps` answers for any owner,
# which `kill -0` does not: it fails EPERM on another user's LIVE pid, and
# reading that as death would delete a running session's tree. Exit 1 from
# `ps` is the "no such process" answer; any other failure means `ps` itself
# was unusable, which is never proof (HATS-1624).
pid_is_dead() {
    local pid="$1" out rc=0 state
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    command -v ps >/dev/null 2>&1 || return 1
    out="$(ps -o state= -p "$pid" 2>/dev/null)" || rc=$?
    if (( rc != 0 )); then
        (( rc == 1 )) && return 0                 # no such process ⇒ dead
        return 1                                  # ps unusable ⇒ keep
    fi
    state="$(printf '%s' "$out" | head -1 | tr -d '[:space:]')"
    # A zombie has exited and only awaits its parent's wait(); its row is
    # otherwise indistinguishable from a live one.
    [[ -z "$state" || "$state" == Z* ]]
}

# Every dir worth judging: worktrees, and each pytest RUN dir (not the root).
candidates=()
for root in "${ROOTS[@]}"; do
    for path in "$root"/ai-hats-wt-*; do
        [[ -d "$path" && ! -L "$path" ]] && candidates+=("$path")
    done
    for parent in "$root"/pytest-of-*; do
        [[ -d "$parent" ]] || continue
        for path in "$parent"/pytest-[0-9]*; do
            [[ -d "$path" && ! -L "$path" ]] && candidates+=("$path")
        done
    done
done

skip_line() {
    printf "  ${YELLOW}skip${RESET} %s ${DIM}(%s)${RESET}\n" "$1" "$2"
}

total=0
freed_kb=0
for path in "${candidates[@]:-}"; do
    [[ -e "$path" ]] || continue
    real="$(cd "$path" 2>/dev/null && pwd -P || echo "$path")"
    if is_in_use "$real"; then
        skip_line "$path" "in use — cwd is inside"
        continue
    fi
    if is_registered_worktree "$real"; then
        skip_line "$path" "live — git still tracks this worktree"
        continue
    fi

    reason=""
    if [[ "${path##*/}" == pytest-[0-9]* ]]; then
        lock="$path/.lock"
        if [[ -f "$lock" ]]; then
            pid="$(tr -dc '0-9' < "$lock" 2>/dev/null || true)"
            if [[ -z "$pid" ]]; then
                skip_line "$path" "lock carries no pid — no proof of death"
                continue
            elif pid_is_dead "$pid"; then
                reason="owner pid $pid exited"
            else
                skip_line "$path" "LIVE — pytest pid $pid is still running"
                continue
            fi
        elif [[ "$FORCE" -eq 1 ]]; then
            reason="no owner lock, --force"
        else
            skip_line "$path" "no owner lock — left to pytest's own rotation"
            continue
        fi
    else
        reason="git no longer tracks this worktree"
    fi

    sz_kb="$(du -sk "$path" 2>/dev/null | cut -f1 || echo 0)"
    total=$((total + 1))
    freed_kb=$((freed_kb + sz_kb))
    if [[ "$DRY" -eq 1 ]]; then
        printf "  ${DIM}would rm${RESET} %s ${DIM}(%s)${RESET}\n" "$path" "$reason"
    else
        rm -rf "$path"
        printf "  ${GREEN}rm${RESET}   %s ${DIM}(%s)${RESET}\n" "$path" "$reason"
    fi
done

freed_mb=$((freed_kb / 1024))
if [[ "$total" -eq 0 ]]; then
    printf "${GREEN}nothing to clean${RESET} (roots: %s)\n" "${ROOTS[*]}"
elif [[ "$DRY" -eq 1 ]]; then
    printf "${BOLD}DRY-RUN${RESET}: %d dir(s), ~%d MB would be freed.\n" "$total" "$freed_mb"
else
    printf "${BOLD}removed %d dir(s), ~%d MB freed${RESET}\n" "$total" "$freed_mb"
fi
