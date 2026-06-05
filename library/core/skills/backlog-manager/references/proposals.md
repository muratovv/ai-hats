# Proposals (`ai-hats task proposal …`)

Proposals (PROP-NNN) are improvement suggestions emitted by reflect-session or other reviewers. Status regulates visibility — accepted/rejected/deferred proposals stay on disk for traceability.

```bash
# Create
ai-hats task proposal create \
  --title "<title>" \
  --category {rule|skill|code|process|doc} \
  --target "<rule/skill/file/process name>" \
  --description "<what>" \
  --rationale "<why>" \
  --related-hypotheses HYP-001,HYP-005 \
  --session <SID>

# List
ai-hats task proposal list --status open --json
ai-hats task proposal list --category rule

# Show
ai-hats task proposal show PROP-001

# +1 vote
ai-hats task proposal vote --prop PROP-001 --session <SID> --reasoning "agree"

# Status
ai-hats task proposal status --prop PROP-001 --status accepted
```
