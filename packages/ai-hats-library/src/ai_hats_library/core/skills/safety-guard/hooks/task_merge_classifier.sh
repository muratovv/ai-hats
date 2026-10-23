#!/usr/bin/env bash
# HATS-2062 — task-merge classifier (sourced by pre_bash_task_merge_guard.sh).
#
# Function: classify_task_merge "<full command string>"
#   echoes one of: task-merge-base | task-merge | safe
#
# Verdicts:
#   task-merge-base — the command merges a task branch AND itself switches to a
#     base branch first (`git checkout master && git merge task/x`). Refuse
#     without any further check: the target is unambiguous.
#   task-merge      — the command merges a task branch (`git merge task/x`)
#     but says nothing about the current branch. The GUARD resolves the cwd's
#     branch: refuse on a base branch, allow elsewhere (merging task→task in a
#     worktree does not blind the review diff).
#   safe            — everything else.
#
# Why this guard exists at all: wtr-style review diffs are computed against the
# merge-base with the base branch. Merging the task branch into master before
# review makes merge-base == branch HEAD, and the review surface goes empty by
# construction (PROX-619). The sanctioned merge path is `ai-hats wt merge`,
# which carries the card-state check and the supervisor's consent.
#
# The classifier inspects the WHOLE command string (catches chaining via
# &&, ||, ;, |, $(...), backticks). It is pure-string: no git calls, so it is
# testable stand-alone — the cwd resolution lives in the guard.

# shellcheck disable=SC2329  # function sourced into other scripts
classify_task_merge() {
    local cmd="$1"

    # A command may begin at the start of the string or after a chain operator.
    # Unlike the shared-state classifier, plain whitespace is NOT a lead: this
    # guard's verdict is a hard deny (no ask), so `echo "git merge task/x"` —
    # prose ABOUT a merge, e.g. a doc or this guard's own routing message —
    # must not match. The cost is missing `xargs git merge …`-style indirection,
    # which is the accepted obfuscation residual from the plan.
    local lead='(^[[:space:]]*|[;&|(`${][[:space:]]*)'
    # Options a binary accepts BEFORE its verb — git's globals (`-C <path>`,
    # `-c k=v`, `--git-dir=…`, `-P`) — each optionally taking one value token.
    local opts='([[:space:]]+-[^[:space:]]+([[:space:]]+[^-[:space:]][^[:space:]]*)?)*'
    local sp='[[:space:]]+'
    # A task-branch ref: task/<prefix>-<number> (task/hats-2062, task/PROX-619).
    # The prefix is the project's ticket prefix, lower- or uppercase. The
    # boundary after the number rejects lookalikes (`task/prox-619ish`) and
    # accepts a chain operator right after the ref (`merge task/x; …`).
    local task_ref='task/[A-Za-z]+-[0-9]+([^A-Za-z0-9/_-]|$)'

    # `git merge` whose segment (up to the next chain operator) mentions a
    # task-branch ref. Options between the verb and the ref are covered by the
    # `[^;&|]*` middle; a task ref in a LATER segment (`git merge master; echo
    # task/x`) does not match.
    local git_merge_task="${lead}git${opts}${sp}merge${sp}[^;&|\`]*${task_ref}"
    # A switch to a base branch anywhere in the string: checkout or switch,
    # flag tokens allowed before the branch name (`checkout -f master`). An
    # over-match here (`checkout -b master x`) costs a refuse the agent can
    # route around via `ai-hats wt merge`, never a silent pass.
    local switch_to_base="${lead}git${opts}${sp}(checkout|switch)([[:space:]]+-[^[:space:]]+)*[[:space:]]+(master|main)([[:space:]]|$)"

    if [[ "$cmd" =~ $git_merge_task ]]; then
        if [[ "$cmd" =~ $switch_to_base ]]; then
            echo task-merge-base
            return 0
        fi
        echo task-merge
        return 0
    fi

    echo safe
    return 0
}

# Stand-alone invocation for ad-hoc testing / debugging:
#   bash task_merge_classifier.sh "git merge task/prox-619"
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    if [[ $# -eq 0 ]]; then
        echo "usage: $0 \"<command string>\"" >&2
        exit 64
    fi
    classify_task_merge "$*"
fi
