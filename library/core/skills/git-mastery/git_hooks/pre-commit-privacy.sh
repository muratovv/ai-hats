#!/usr/bin/env bash
# HATS-083 — privacy review pre-commit hook (managed by ai-hats / git-mastery skill)
#
# Scans staged additions for content that should not land in a public repo:
# absolute home paths, API key prefixes, .env-style secrets, raw email
# addresses, and large new files in tests/fixtures/. Soft warnings are
# printed; hard hits abort the commit.
#
# False-positive escape valves, narrowest first:
#   Inline marker (one line):   append  # ai-hats: allow-secret  to a confirmed
#                               false-positive line — only that line is skipped.
#   Allowlist (whole file):     .privacy-allowlist (project root or .githooks/),
#                               one path-glob per line, '#' for comments.
#   Override (whole commit):    AI_HATS_PRIVACY_ACK=1 git commit ...
set -uo pipefail

# HATS-633 — the inline allow-marker string, matched literally (grep -F).
PRIVACY_ALLOW_MARKER='ai-hats: allow-secret'

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

hard_hits=()
soft_hits=()

# Patterns (extended regex; assume Linux/BSD compatibility).
PAT_HOME='/(Users|home)/[A-Za-z0-9_.-]+/'
PAT_API_KEY='(sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{36}|AKIA[A-Z0-9]{16}|xox[bp]-[A-Za-z0-9-]+|AIza[A-Za-z0-9_-]{20,}|glpat-[A-Za-z0-9_-]{20,})'
PAT_BEARER='[Aa]uthorization:[[:space:]]*[Bb]earer[[:space:]]+[A-Za-z0-9._-]{20,}'
PAT_ENV='^[+][[:space:]]*[A-Z][A-Z0-9_]*_(KEY|TOKEN|SECRET|PASSWORD|PASSWD|API)='
PAT_EMAIL='[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'
# Claude Code session markers — JSONL/trace fragments that almost always
# carry sessionId, requestId, cwd, and unredacted user prompts.
PAT_CLAUDE_SESSION='"(sessionId|requestId)":[[:space:]]*"[A-Za-z0-9_-]{8,}"'
PAT_CLAUDE_CWD='"cwd":[[:space:]]*"/'
# Structural markers unique to Claude Code recordings — catches recordings
# that were anonymized (sessionId stripped) but kept the JSONL skeleton.
PAT_CLAUDE_SCHEMA='"(parentUuid|toolUseResult|sourceToolAssistantUUID)":'

# HATS-633 — credential catalogue. Pattern *ideas* derived from parry (MIT);
# every regex re-written here and kept to `grep -E` (BSD/GNU portable). The
# DeBERTa ML model + tree-sitter exfil analysis from parry are deliberately
# NOT borrowed — too heavy a dependency for a pre-commit hook.
PAT_PRIVATE_KEY='-----BEGIN ([A-Z0-9]+ )?PRIVATE KEY-----'  # ai-hats: allow-secret
# DB connection URI carrying embedded credentials (scheme://user:pass@host).
# Requires user:pass@ so a credential-free `postgres://localhost` does NOT hit.
PAT_DB_URI='(postgres|postgresql|mysql|mongodb(\+srv)?|redis|rediss|amqps?)://[^:@/[:space:]]+:[^@/[:space:]]+@'
# GitHub token family beyond the ghp_ classic already in PAT_API_KEY.
PAT_GITHUB_TOKEN='(gh[oprsu]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{22,})'  # ai-hats: allow-secret
# AWS secret access key — contextual (key name + 40-char base64) to keep FP low;
# the bare AKIA access-key id is already covered by PAT_API_KEY.
PAT_AWS_SECRET='[Ss][Ee][Cc][Rr][Ee][Tt][_-]?[Aa][Cc][Cc][Ee][Ss][Ss][_-]?[Kk][Ee][Yy][^A-Za-z0-9]{1,6}[A-Za-z0-9/+]{40}'  # ai-hats: allow-secret
PAT_SLACK_WEBHOOK='https://hooks\.slack\.com/services/T[A-Za-z0-9_]+/B[A-Za-z0-9_]+/[A-Za-z0-9]+'
PAT_STRIPE='(sk|rk)_live_[A-Za-z0-9]{24,}'
PAT_SENDGRID='SG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}'
PAT_NPM='npm_[A-Za-z0-9]{36}'
# JWT — three base64url segments, first two starting eyJ ('{"' base64). Length
# floors keep ordinary dotted identifiers from matching.
PAT_JWT='eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}'

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

    # HATS-633 — drop marker-bearing lines (see PRIVACY_ALLOW_MARKER at top)
    # before scanning, so only that one line is skipped. The marker is only ever
    # seen on added lines here, so it cannot whitelist pre-existing content.
    added="$(printf '%s\n' "$added" | grep -Fv "$PRIVACY_ALLOW_MARKER")"
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
claude session id|$PAT_CLAUDE_SESSION
claude session cwd|$PAT_CLAUDE_CWD
claude jsonl schema|$PAT_CLAUDE_SCHEMA
private key header|$PAT_PRIVATE_KEY
db connection uri|$PAT_DB_URI
github token|$PAT_GITHUB_TOKEN
aws secret key|$PAT_AWS_SECRET
slack webhook|$PAT_SLACK_WEBHOOK
stripe live key|$PAT_STRIPE
sendgrid key|$PAT_SENDGRID
npm token|$PAT_NPM
jwt|$PAT_JWT
EOF

    # Soft warning: large new file in tests/fixtures/
    if [[ "$file" == tests/fixtures/* ]] && git diff --cached --diff-filter=A --name-only -- "$file" | grep -q .; then
        size_bytes=$(wc -c < "$file" 2>/dev/null || echo 0)
        if (( size_bytes > 5120 )); then
            soft_hits+=("$file: new fixture > 5KB ($size_bytes bytes) — synthetic fixtures are usually <1KB; verify it contains no real session content")
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
    cat >&2 <<'EOF'

Resolve in this order — do NOT reflexively bypass; a real secret must never land:
  1. If it IS a real secret, remove it from the staged content and re-commit.
  2. Confirmed false-positive on ONE line? Append an inline marker to that line
     so only it is skipped:   # ai-hats: allow-secret
  3. A whole known-safe file? Add a glob to .privacy-allowlist.
  4. Last resort — whole commit, and only after telling the user what was flagged:
       AI_HATS_PRIVACY_ACK=1 git commit ...
EOF
    exit 1
fi

exit 0
