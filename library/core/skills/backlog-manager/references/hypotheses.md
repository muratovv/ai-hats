# Hypotheses (`ai-hats task hyp …`)

Hypotheses (HYP-NNN) are proposed changes with measurable expectations. Verdicts come from `reflect-session` runs on session retros. Status flips manually via CLI after the validation window closes.

## Field contract (read before authoring)

The four creation fields carry distinct semantic loads. Mixing them up
produces HYPs that get closed `refuted` because the wrong thing was
being tested. The audit of HYP-001..026 found ~50% of HYPs filed with
fields confused or redundant.

| Field | Carries | NOT |
|---|---|---|
| `--title` | The **class** of behaviour (broad enough to cover adjacent flavors). | A single-incident description. |
| `--hypothesis` | The **mechanism** — *why* the agent does X. The CAUSE from self-retro 4.5.b. | A restatement of the observation. |
| `--baseline` | The **observation** — concrete cited symptoms from sessions. The WHAT WENT WRONG trace. | A re-paraphrase of the hypothesis. |
| `--success-criterion` | How a future auditor decides confirmed/refuted, in countable terms. | A wish ("agent improves"). |

Sanity check: read `hypothesis` and `baseline` side by side. If they
say the same thing in different words, the mechanism wasn't reached —
walk one more "why?" via self-retro 4.5.b before filing.

### ✓ Good: class title + mechanism hypothesis (HYP-022)

- `title`: "agent re-emits same class of shell-quoting bug across
  adjacent flavors despite knowing one — does not adopt minimum-risk
  heredoc form by default" — names a **class**, not one incident.
- `hypothesis`: "agent knows ONE specific shell-quoting hazard and
  fixes that one, but adjacent flavors (nested EOF, dollar-sign
  interpolation, glob expansion) recur because the agent doesn't
  generalize to the minimum-risk form: unique heredoc marker that does
  not appear in the body, single-quoted to disable all interpolation" —
  this is a **mechanism** (one-shot lesson instead of class
  discipline), not a paraphrase of the symptoms.
- `baseline`: concrete trace — "Library-curation pass 1 session: hit
  shell-quoting bug twice. (1) HATS-528 task create with double-quoted
  heredoc + backtick code fences → zsh cmd-sub evaluated YAML lines.
  (2) Same session, HATS-537 with single-quoted heredoc but nested
  example EOF terminator closed outer heredoc early. ~10 min recovery."

### ✗ Bad: thin "assumes X" passed off as mechanism (HYP-023)

- `title`: "Agent manually merges task branch before FSM transition,
  triggering double-merge conflict" — describes ONE incident, not a
  class.
- `hypothesis`: "Agent assumes `task transition done` requires the task
  branch to be pre-merged to master" — sounds like a mechanism but is
  surface. The HYP closed `refuted` because the real mechanism was
  muscle-memory git workflow reflex, not an articulated assumption
  about FSM contracts.
- **Correct response:** walk 5 Whys further — *why* would the agent
  assume that? Answer: pre-FSM git workflow reflex (`merge --no-ff`
  + push is the universal "ship a feature branch" pattern; FSM
  internalisation is shallow). That mechanism, framed as a class,
  would have covered every FSM-vs-git-reflex collision, not just the
  pre-merge case.

```bash
# Create a new hypothesis (auto-id, status=active)
ai-hats task hyp create \
  --title "Short title" \
  --hypothesis "<expected improvement statement>" \
  --source-task PROJ-042 \
  --baseline "<measurable starting state>" \
  --expected-outcome "<bullet 1>" --expected-outcome "<bullet 2>" \
  --observation-window "4 sessions" \
  --success-criterion "<how a verdict is decided>"

# List
ai-hats task hyp list                         # all
ai-hats task hyp list --status active --json  # filter

# Show
ai-hats task hyp show HYP-001

# Append a verdict (atomic, filelock-protected)
ai-hats task hyp append-verdict \
  --hyp HYP-001 --session <SID> \
  --verdict {confirmed|refuted|inconclusive|n/a} \
  --evidence "<one-line citation>" \
  --recommendation {close_confirmed|close_refuted|keep|extend_window}

# Flip status (after the validation window closes)
ai-hats task hyp set-status --hyp HYP-001 --status confirmed
ai-hats task hyp set-status --hyp HYP-001 --status refuted
ai-hats task hyp set-status --hyp HYP-001 --status stalled

# One-shot normalize all HYP-*.yaml under current schema (idempotent)
ai-hats task hyp migrate [--dry-run]
```

**Discovery flow** — when to create:
- After spotting a recurring pattern across session retros (≥3 sessions where the same friction shows up).
- After a fix lands and you want to validate it.
- Always pair with a `--source-task` so the change is traceable.

**Closing flow** — when to flip status:
- After the validation window declared on creation has filled (`--observation-window`).
- Final `append-verdict` carries a terminal `--recommendation` (`close_confirmed` / `close_refuted`).
- Then `hyp set-status` flips `active → confirmed | refuted | stalled` (the verdict CLI does NOT auto-flip status).

**Cross-project hypotheses:** before parking a hypothesis as "untestable from this repo" or starting a retirement clock for lack of evidence, ASK the user whether sibling projects exist where the hypothesis is testable. Do NOT auto-survey other directories without confirmation.
