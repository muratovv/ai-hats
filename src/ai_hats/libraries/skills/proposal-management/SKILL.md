# Proposal Management

Create and vote on improvement proposals
(`.agent/backlog/proposals/PROP-*.yaml`) during a reflect-session run.

> **Harness shell prelude.** Before any `ai-hats` invocation:
> ```bash
> AH="$(command -v ai-hats || echo ./.venv/bin/ai-hats)"
> ```

## When to Use

You are running as **reflect-session** and you noticed an improvement
opportunity (rule wording, skill update, code bug, process gap, doc fix).

## Procedure

### Step 1 — Read the inbox first

```bash
"$AH" proposal list --status open
```

A proposal is "similar" if a duplicate (`category` + `target`) covers the
same change. Vote rather than create whenever in doubt.

### Step 2a — Vote (preferred path)

```bash
"$AH" proposal vote --prop PROP-NNN \
  --session "$SID" --reasoning "saw same gap in turn 3 — agree with target"
```

Mirror in `proposal_actions` frontmatter:
```yaml
proposal_actions:
  - action: voted
    prop_id: PROP-NNN
```

### Step 2b — Create only if novel

```bash
"$AH" proposal create \
  --title "Explicit Bash anti-pattern enumeration in dev_rule_tool_call_hygiene" \
  --category rule \
  --target dev_rule_tool_call_hygiene \
  --description "Add forbidden-pattern table (grep|find|ls -R|cat|head|tail|sed|awk -i) paired with dedicated-tool replacement." \
  --rationale "5/5 LLM sessions (HYP-008 baseline) substituted Bash for Read/Grep — current rule too abstract." \
  --related-hypotheses HYP-008 \
  --session "$SID"
```

The CLI returns `PROP-NNN`. Mirror in `proposal_actions`:
```yaml
proposal_actions:
  - action: created
    prop_id: PROP-NNN
```

### Step 3 — Meta-proposal (when YOU are the problem)

If you cannot follow the format, the inbox is unparseable, or instructions
conflict — **do not silently drop the entry**. File a meta-proposal:

```bash
"$AH" proposal create \
  --category process --target reflect-session \
  --title "Output format ambiguous for HYP-005 with no session retro" \
  --description "Skill says cite session retro path, but session has no retro yet — fallback unclear." \
  --rationale "Encountered while voting on HYP-005; ambiguity wasted 2 retries." \
  --session "$SID"
```

Add the resulting `PROP-NNN` to `self_problems`:
```yaml
self_problems:
  - PROP-NNN
```

Even if you fail to file the meta-proposal yourself, the runtime post-validator
will create one with `failed_session_id=<sid>` so the failure surfaces in
the inbox.

## Field reference

| Field | Required | Notes |
|---|---|---|
| `--title` | yes | Imperative, concrete, ≤100 chars |
| `--category` | yes | `rule`, `skill`, `code`, `process`, `doc` |
| `--target` | yes | The thing being changed (file path, skill name, rule name) |
| `--description` | yes | What the change is (what, not why) |
| `--rationale` | yes | Why — cite session evidence |
| `--related-hypotheses` | optional | Comma-separated `HYP-NNN[,...]` |
| `--session` | yes | Source session id |

## Examples

### ✓ Good: vote on similar

Existing `PROP-003` open: `category=rule, target=dev_rule_backlog_discipline, title="Forbid Edit/Write under .agent/backlog/tasks"`.
You spot the same issue (agent edited `plan.md` directly). → vote.

### ✓ Good: create novel

Inbox has nothing on `target=src/ai_hats/cli/_helpers.py`. You found that
`exec_claude_with_retro` ignores `--prompt-suffix`. → create with `category=code`.

### ✗ Bad: duplicate creation

Inbox already has `PROP-003` covering rule_X update. You create `PROP-008`
about rule_X with a slightly different title. **Outcome**: noise, vote
fragmentation, reflect-all triage gets harder. → vote on PROP-003 instead.

### ✗ Bad: meta-proposal as blame

Bad: `--title "judge sub-agent is broken"`, no actionable description.
Good: `--title "judge prompt missing pointer to bundle scope"`,
description names the missing line, rationale points at audit/Turn evidence.
