#!/usr/bin/env bash
# HATS-437 — shared-state-write classifier (sourced by both PreToolUse and
# git pre-push hooks).
#
# Function: classify_command "<full command string>"
#   echoes one of: irreversible | shared | safe
#
# Classification:
#   irreversible — no undo path. Hook should BLOCK without explicit ack.
#     - gh pr merge ...           (any variant; --delete-branch is worst case
#                                   but same classification — block is binary)
#     - git push --force / -f / --force-with-lease
#
#   shared       — writes a shared resource but reversible. Rule (Level 2)
#                  asks agent to pause; hook does NOT block. Returned for
#                  completeness so callers can distinguish.
#     - gh pr create / close
#     - gh issue comment
#     - gh release create
#     - git push (regular)
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

    # --- irreversible ---
    # gh pr merge (any variant of "gh<sp>pr<sp>merge" with at least one space
    # separator; tolerates leading subshells / chained operators).
    if [[ "$cmd" =~ (^|[\;\&\|\(\`\$\{[:space:]])gh[[:space:]]+pr[[:space:]]+merge([[:space:]]|$) ]]; then
        echo irreversible
        return 0
    fi

    # git push --force / -f / --force-with-lease
    # We match "git<sp>push" followed (anywhere later, same logical command)
    # by --force / --force-with-lease / -f as a standalone token.
    if [[ "$cmd" =~ (^|[\;\&\|\(\`\$\{[:space:]])git[[:space:]]+push([[:space:]]|$) ]]; then
        # Filter on the force flags as standalone tokens.
        if [[ "$cmd" =~ (^|[[:space:]])(--force|--force-with-lease|-f)([[:space:]=]|$) ]]; then
            echo irreversible
            return 0
        fi
    fi

    # --- shared ---
    if [[ "$cmd" =~ (^|[\;\&\|\(\`\$\{[:space:]])gh[[:space:]]+pr[[:space:]]+(create|close)([[:space:]]|$) ]]; then
        echo shared
        return 0
    fi
    if [[ "$cmd" =~ (^|[\;\&\|\(\`\$\{[:space:]])gh[[:space:]]+issue[[:space:]]+comment([[:space:]]|$) ]]; then
        echo shared
        return 0
    fi
    if [[ "$cmd" =~ (^|[\;\&\|\(\`\$\{[:space:]])gh[[:space:]]+release[[:space:]]+create([[:space:]]|$) ]]; then
        echo shared
        return 0
    fi
    if [[ "$cmd" =~ (^|[\;\&\|\(\`\$\{[:space:]])git[[:space:]]+push([[:space:]]|$) ]]; then
        echo shared
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
