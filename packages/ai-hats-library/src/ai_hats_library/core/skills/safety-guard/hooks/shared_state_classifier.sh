#!/usr/bin/env bash
# HATS-437 — shared-state-write classifier (sourced by both PreToolUse and
# git pre-push hooks).
#
# Function: classify_command "<full command string>"
#   echoes one of: irreversible | gated | shared | safe
#
# Classification:
#   irreversible — no undo path. Hook should BLOCK without explicit ack.
#     - gh pr merge ...           (any variant; --delete-branch is worst case
#                                   but same classification — block is binary)
#     - git push --force / -f / --force-with-lease
#
#   gated        — reversible, but must not happen without the supervisor's
#                  go-ahead. Hook BLOCKS; the ack flag opens it (HATS-1253).
#     - git push (regular)
#
#   shared       — writes a shared resource but reversible. Rule (Level 2)
#                  asks agent to pause; hook does NOT block. Returned for
#                  completeness so callers can distinguish.
#     - gh pr create / close
#     - gh issue comment
#     - gh release create
#
#   safe         — everything else.
#
# The classifier inspects the WHOLE command string (so it catches commands
# chained via &&, ||, ;, |, $(...), backticks). The Level 2 rule explicitly
# forbids that chaining; the classifier's job is to be a safety net when the
# rule is violated.

# shellcheck disable=SC2329  # function sourced into other scripts
classify_command() {
    local cmd="$1"

    # A command may begin at the start of the string or after a chain operator.
    local lead='(^|[;&|(`${[:space:]])'
    # Options a binary accepts BEFORE its verb — git's globals (`-C <path>`,
    # `-c k=v`, `--git-dir=…`, `-P`), gh's `-R <repo>` — each optionally taking
    # one value token. Generic on purpose: git and gh refuse an unknown option
    # themselves, and an over-match here costs an `ask`, never a deny.
    local opts='([[:space:]]+-[^[:space:]]+([[:space:]]+[^-[:space:]][^[:space:]]*)?)*'
    # The verb is a whole token: `push` but not `push.default` or `pushd`.
    local end='([[:space:]]|$)'
    local sp='[[:space:]]+'
    local git_push="${lead}git${opts}${sp}push${end}"
    local gh_pr_merge="${lead}gh${opts}${sp}pr${opts}${sp}merge${end}"
    local gh_pr_open_close="${lead}gh${opts}${sp}pr${opts}${sp}(create|close)${end}"
    local gh_issue_comment="${lead}gh${opts}${sp}issue${opts}${sp}comment${end}"
    local gh_release_create="${lead}gh${opts}${sp}release${opts}${sp}create${end}"

    # --- irreversible ---
    # gh pr merge (any variant of "gh<sp>pr<sp>merge" with at least one space
    # separator; tolerates leading subshells / chained operators).
    if [[ "$cmd" =~ $gh_pr_merge ]]; then
        echo irreversible
        return 0
    fi

    # git push --force / -f / --force-with-lease
    # We match "git<sp>push" followed (anywhere later, same logical command)
    # by --force / --force-with-lease / -f as a standalone token.
    if [[ "$cmd" =~ $git_push ]]; then
        # Filter on the force flags as standalone tokens.
        if [[ "$cmd" =~ (^|[[:space:]])(--force|--force-with-lease|-f)([[:space:]=]|$) ]]; then
            echo irreversible
            return 0
        fi
        # HATS-1294: --force is not the only spelling of a force push. A leading
        # '+' on a refspec forces just the same, and --mirror force-updates every
        # ref (deleting the ones absent locally). Both used to fall through to
        # `gated`, which named the wrong hazard in the refusal and let a single
        # ack cover an ordinary push and a history overwrite alike.
        if [[ "$cmd" =~ (^|[[:space:]])\+[^[:space:]]+([[:space:]]|$) ]]; then
            echo irreversible
            return 0
        fi
        if [[ "$cmd" =~ (^|[[:space:]])--mirror([[:space:]]|$) ]]; then
            echo irreversible
            return 0
        fi
        # Deleting a remote branch: --delete/-d, or an empty-source refspec
        # (`git push origin :master`). The ref is gone for everyone downstream.
        if [[ "$cmd" =~ (^|[[:space:]])(--delete|-d)([[:space:]]|$) ]] \
            || [[ "$cmd" =~ (^|[[:space:]]):[^[:space:]]+([[:space:]]|$) ]]; then
            echo irreversible
            return 0
        fi
    fi

    # --- shared ---
    if [[ "$cmd" =~ $gh_pr_open_close ]]; then
        echo shared
        return 0
    fi
    if [[ "$cmd" =~ $gh_issue_comment ]]; then
        echo shared
        return 0
    fi
    if [[ "$cmd" =~ $gh_release_create ]]; then
        echo shared
        return 0
    fi
    # --- gated ---
    if [[ "$cmd" =~ $git_push ]]; then
        # --dry-run contacts the remote but writes nothing. Checked HERE, not
        # globally: the irreversible patterns above have already returned, so
        # a chained `git push --dry-run && gh pr merge` cannot mask itself.
        if [[ "$cmd" =~ (^|[[:space:]])--dry-run([[:space:]]|$) ]]; then
            echo safe
            return 0
        fi
        echo gated
        return 0
    fi

    echo safe
    return 0
}

# Stand-alone invocation for ad-hoc testing / debugging:
#   bash shared_state_classifier.sh "gh pr merge 1 --merge"
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    if [[ $# -eq 0 ]]; then
        echo "usage: $0 \"<command string>\"" >&2
        exit 64
    fi
    classify_command "$*"
fi
