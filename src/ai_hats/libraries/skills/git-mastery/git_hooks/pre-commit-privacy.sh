#!/usr/bin/env bash
# HATS-083 — privacy review pre-commit hook (managed by ai-hats / git-mastery skill)
#
# Scans staged additions for content that should not land in a public repo:
# absolute home paths, API key prefixes, .env-style secrets, raw email
# addresses, and large new files in tests/fixtures/. Soft warnings are
# printed; hard hits abort the commit.
#
# Override (per single commit):  AI_HATS_PRIVACY_ACK=1 git commit ...
# Allowlist file (project root or .githooks/):  .privacy-allowlist
#   one path-glob per line, '#' for comments
set -uo pipefail

if [[ "${AI_HATS_PRIVACY_ACK:-}" == "1" ]]; then
    echo "[privacy] override acknowledged via AI_HATS_PRIVACY_ACK=1" >&2
    exit 0
fi

# Locate allowlist (root, then .githooks/).
allowlist=""
for candidate in ".privacy-allowlist" ".githooks/.privacy-allowlist"; do
    if [[ -f "$candidate" ]]; then
        allowlist="$candidate"
        break
    fi
done

# Helper: returns 0 if path matches any allowlist glob.
is_allowlisted() {
    local path="$1"
    [[ -z "$allowlist" ]] && return 1
    while IFS= read -r line; do
        [[ -z "$line" || "$line" == \#* ]] && continue
        # shellcheck disable=SC2053
        if [[ "$path" == $line ]]; then
            return 0
        fi
        # Match basename too.
        if [[ "$(basename "$path")" == $line ]]; then
            return 0
        fi
    done < "$allowlist"
    return 1
}

# Collect staged file list (added/modified/copied).
staged="$(git diff --cached --name-only --diff-filter=ACM)"
[[ -z "$staged" ]] && exit 0

declare -a hard_hits
declare -a soft_hits

# Patterns (extended regex; assume Linux/BSD compatibility).
PAT_HOME='/(Users|home)/[A-Za-z0-9_.-]+/'
PAT_API_KEY='(sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{36}|AKIA[A-Z0-9]{16}|xox[bp]-[A-Za-z0-9-]+|AIza[A-Za-z0-9_-]{20,}|glpat-[A-Za-z0-9_-]{20,})'
PAT_BEARER='[Aa]uthorization:[[:space:]]*[Bb]earer[[:space:]]+[A-Za-z0-9._-]{20,}'
PAT_ENV='^[+][[:space:]]*[A-Z][A-Z0-9_]*_(KEY|TOKEN|SECRET|PASSWORD|PASSWD|API)='
PAT_EMAIL='[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'

while IFS= read -r file; do
    [[ -z "$file" ]] && continue
    if is_allowlisted "$file"; then
        continue
    fi

    # Skip binary files (git diff handles --text by default for the diff body
    # but git classifies binary on its own; we read added lines via --diff-filter).
    diff_body="$(git diff --cached -U0 --text -- "$file" 2>/dev/null || true)"
    [[ -z "$diff_body" ]] && continue
    # Only consider added lines (start with `+` but not `+++`).
    added="$(printf '%s\n' "$diff_body" | grep -E '^\+' | grep -Ev '^\+\+\+ ')"
    [[ -z "$added" ]] && continue

    while IFS= read -r pattern_label; do
        IFS='|' read -r label pattern <<< "$pattern_label"
        if printf '%s\n' "$added" | grep -Eq -- "$pattern"; then
            sample="$(printf '%s\n' "$added" | grep -E -- "$pattern" | head -1 | sed 's/^+//' | cut -c1-100)"
            hard_hits+=("$file: $label — $sample")
        fi
    done <<EOF
absolute home path|$PAT_HOME
api key|$PAT_API_KEY
bearer token|$PAT_BEARER
env-style secret|$PAT_ENV
email address|$PAT_EMAIL
EOF

    # Soft warning: large new file in tests/fixtures/
    if [[ "$file" == tests/fixtures/* ]] && git diff --cached --diff-filter=A --name-only -- "$file" | grep -q .; then
        size_bytes=$(wc -c < "$file" 2>/dev/null || echo 0)
        if (( size_bytes > 10240 )); then
            soft_hits+=("$file: new fixture > 10KB ($size_bytes bytes) — verify it contains no real session content")
        fi
    fi
done <<< "$staged"

# Print soft warnings (non-blocking).
if (( ${#soft_hits[@]} > 0 )); then
    echo "[privacy] warnings:" >&2
    for w in "${soft_hits[@]}"; do
        echo "  - $w" >&2
    done
fi

# Hard hits → block commit.
if (( ${#hard_hits[@]} > 0 )); then
    echo "" >&2
    echo "[privacy] commit blocked — potential leaks in staged content:" >&2
    for h in "${hard_hits[@]}"; do
        echo "  ! $h" >&2
    done
    echo "" >&2
    echo "If these are intentional/false-positive, override with:" >&2
    echo "  AI_HATS_PRIVACY_ACK=1 git commit ..." >&2
    echo "Or add a glob to .privacy-allowlist for known-safe files." >&2
    exit 1
fi

exit 0
