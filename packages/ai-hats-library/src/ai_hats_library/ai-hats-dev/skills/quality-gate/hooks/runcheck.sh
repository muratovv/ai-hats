#!/usr/bin/env bash
# HATS-1709 — run a check so its OWN status is what everyone reads.
#
# The harness announces a backgrounded command by its last element, faithfully.
# That is why the prescribed capture form is also the masking form: `<runner>;
# echo $? > rc` ends in the echo, so a red tier arrives as "exit code 0" while
# the file next to it says 1. Both channels can be right at once — the capture
# has to go INSIDE and the status has to come OUT:
#
#   timeout 1800 bash <this> --log /tmp/e2e.log -- bash scripts/gates.sh e2e
#
# The bound stays outside on purpose. `has_timeout_wrapper` in the
# command-lifetime guard is lexical, so `--timeout` as a flag here would read as
# no bound at all — and the number belongs to the agent anyway.
#
# No `-e`: the runner returning non-zero is this script's OUTPUT, not a reason to
# abort before recording it.
set -uo pipefail

usage() {
    [[ -n "${1:-}" ]] && printf 'runcheck: %s\n\n' "$1" >&2
    cat >&2 <<'USAGE'
usage: timeout <seconds> bash runcheck.sh --log <path> -- <command> [args...]

  --log <path>   where the run's output goes; its status lands in <path>.rc
  --             everything after it is the command, run with no shell between

Exits with the command's own status. Read <path>.rc, never the completion
notice: for a background launch that notice reports this wrapper, and only a
wrapper that ends in the command makes the two agree.
USAGE
    exit 2
}

log=""
saw_sep=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --log)
            [[ $# -ge 2 ]] || usage "--log needs a path"
            log="$2"
            shift 2
            ;;
        --log=*)
            log="${1#--log=}"
            shift
            ;;
        --)
            saw_sep=1
            shift
            break
            ;;
        -h | --help) usage ;;
        # A bare word here is the command starting one token early — naming the
        # missing separator beats reporting it as an option nobody wrote.
        -*) usage "unknown option: $1" ;;
        *) usage "missing -- before the command" ;;
    esac
done

[[ -n "$log" ]] || usage "--log is required — the caller says where to read the run"
[[ -n "$saw_sep" ]] || usage "missing -- before the command"
[[ $# -gt 0 ]] || usage "no command after --"

rc_file="${log}.rc"

# Clearing BOTH before the run is the whole freshness guarantee: a reader that
# finds an rc file knows it belongs to this run, and one that finds none knows
# the run did not finish (the outer timeout fired) rather than reading last
# run's verdict as today's.
mkdir -p -- "$(dirname -- "$log")" || usage "cannot create the log's directory"
rm -f -- "$log" "$rc_file"

# `"$@"` with no shell between: a pipe cannot enter here, so the whole class of
# pipe masking is gone by construction rather than by review.
"$@" > "$log" 2>&1
rc=$?

printf '%s\n' "$rc" > "$rc_file"
printf 'runcheck: rc=%s\nruncheck: log=%s\nruncheck: rc_file=%s\n' "$rc" "$log" "$rc_file"
exit "$rc"
