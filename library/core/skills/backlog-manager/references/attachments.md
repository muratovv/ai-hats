# Attachments (`ai-hats task attach …`)

Attach files (plans, diagrams, sample inputs) to a task card. Blob lives in
`<ai_hats_dir>/tracker/backlog/tasks/<ID>/attachments/<name>`; the manifest
entry (`name`, `digest`, `added`, `note`) is stored in `task.yaml::attachments[]`.
Works on tasks in **any** state, including `done` / `cancelled` — postmortem
artifacts and late corrections are intentional.

```bash
# Move a file into the task's attachments/ and record it in the manifest.
# Idempotent: re-running with identical content is a no-op.
ai-hats task attach add PROJ-042 /path/to/plan.md --note "design draft"

# Override the attachment name (default: basename)
ai-hats task attach add PROJ-042 /tmp/abc.tmp --name diagram.d2

# List
ai-hats task attach list PROJ-042

# Print content to stdout (binaries print only the path + warning)
ai-hats task attach show PROJ-042 plan.md

# Detach (also deletes the blob)
#   tracked blob → silent remove (recoverable via 'git restore')
#   untracked   → requires --yes (deletion is permanent)
ai-hats task attach remove PROJ-042 plan.md
ai-hats task attach remove PROJ-042 plan.md --yes
```

**Idempotency & conflicts.** `attach add` is intentionally idempotent for the
common case — re-attaching the same file with the same name is a no-op. But
attaching **different** content under an **existing** name is a hard error,
not a silent overwrite. To replace: `attach remove` first, then `attach add`.

**Pre-commit guard (HATS-402).** A pre-commit hook installed by this skill
blocks commits that add or modify files under `tasks/<ID>/attachments/`
without a matching manifest entry. The fix is always the same: run
`attach add` to register the file. Per-commit override:
`AI_HATS_ATTACH_ACK=1 git commit …`.

**Digest.** The recorded `digest` is the first 12 hex chars of the blob's
SHA-256 — full hash would balloon `task.yaml` and waste agent context on
every read. 48 bits gives a birthday-safe namespace of ~2^24 attachments
per task, well beyond any realistic scale.
