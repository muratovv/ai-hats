# Review Proposal

Triage one improvement opportunity → vote on existing PROP or create new.
Role-agnostic: same procedure whether you are running as `reflect-session`,
`session-reviewer`, `judge`, or any other reviewer.

> **Harness shell prelude.** Before any `ai-hats` invocation:
> ```bash
> AH="$(command -v ai-hats || echo ./.venv/bin/ai-hats)"
> ```

## When to Use

You spotted an improvement opportunity (rule wording, skill update, code
bug, process gap, doc fix), or you are sweeping the open inbox for triage
(`open → accepted | rejected | deferred | duplicate`).

## Procedure

### Step 1 — Read the inbox first

```bash
"$AH" task proposal list --status open
```

A proposal is "similar" if it covers the same change (same `category` +
`target`). **Vote rather than create whenever in doubt** — duplicates
fragment the signal.

### Step 2a — Vote (preferred)

```bash
"$AH" task proposal vote --prop PROP-NNN \
  --session "$SID" --reasoning "<one-line: why you agree>"
```

### Step 2b — Create only if novel

```bash
"$AH" task proposal create \
  --title "<short imperative title>" \
  --category {rule|skill|code|process|doc} \
  --target "<rule/skill/file/process name>" \
  --description "<what the change is — what, not why>" \
  --rationale "<why — cite session evidence>" \
  --related-hypotheses HYP-NNN[,HYP-MMM] \
  --session "$SID"
```

The CLI returns the new `PROP-NNN`.

### Step 3 — Triage open proposals (judge / triage roles only)

If you are the role responsible for closing the inbox (typically `judge`),
flip status after weighing votes/evidence:

```bash
"$AH" task proposal status --prop PROP-NNN --status accepted
"$AH" task proposal status --prop PROP-NNN --status rejected
"$AH" task proposal status --prop PROP-NNN --status deferred
"$AH" task proposal status --prop PROP-NNN --status duplicate
```

### Step 4 — Meta-proposal (when YOU are the problem)

If you cannot follow the format, the inbox is unparseable, or the
instructions conflict — **do NOT silently drop the entry**. File a
meta-proposal:

```bash
"$AH" task proposal create \
  --category process --target <your-role> \
  --title "<one-line: what failed>" \
  --description "<what>" --rationale "<why it blocked you>" \
  --session "$SID"
```

Even if you fail to file the meta-proposal yourself, the runtime
post-validator will create one with `failed_session_id=<sid>` so the
failure surfaces in the inbox.

## Output handoff

How the action is *reported* depends on the calling role:

- **Running as `reflect-session` / `session-reviewer`** — mirror in the
  `proposal_actions` array of the session document; see **review-session**.
- **Running as `judge`** — list each create/vote/status flip in the judge
  report (`.agent/retrospectives/judge/<UTC-ISO-ts>-report.md`).

## Field reference

| Field | Required | Notes |
|---|---|---|
| `--title` | yes | Imperative, concrete, ≤100 chars |
| `--category` | yes | `rule`, `skill`, `code`, `process`, `doc` |
| `--target` | yes | The thing being changed (file path, skill/rule/role name) |
| `--description` | yes | What the change is (what, not why) |
| `--rationale` | yes | Why — cite session evidence |
| `--related-hypotheses` | optional | Comma-separated `HYP-NNN[,...]` |
| `--session` | yes | Source session id |

## Examples

### ✓ Good: vote on similar

Existing `PROP-003` open: `category=rule, target=rule_backlog_discipline`,
covers your concern. → vote with one-line reasoning.

### ✓ Good: create novel

Inbox has nothing on `target=src/ai_hats/cli/_helpers.py`. You found that
`exec_claude_with_retro` ignores `--prompt-suffix`. → create with
`category=code`.

### ✗ Bad: duplicate creation

Inbox already has `PROP-003` covering rule_X. You create `PROP-008` about
rule_X with a slightly different title. **Outcome**: vote fragmentation,
triage gets harder. → vote on PROP-003 instead.

### ✗ Bad: meta-proposal as blame

Bad: `--title "reflect-session is broken"`, no actionable description.
Good: `--title "reflect-session skill missing pointer to inbox-first rule"`,
description names the missing line, rationale points at audit/Turn evidence.
