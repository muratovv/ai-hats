# Destructive Actions & Data Protection

## 1. Protected Data (Sacred Files)

**Protected = anything that cannot be regenerated from the repository plus the
toolchain** — persistent state, secrets, captured runs, human-authored notes.
Being untracked or gitignored makes a file *more* at risk, not less: nothing
can restore it. The names below are a fast path, not the definition; when a
path is not on the list, apply the test.

- `.db`, `.sqlite`, `.sqlite3`, `.sql`, `.dump`
- `volumes/`, `data/`, `storage/`, `runs/` directories
- `terraform.tfstate`, `.env`

**Mandatory Action**: NEVER delete, overwrite, or move these files without explicit written confirmation from the user.

## 2. Before Destructive Operations

Before any action that destroys resources (files, VMs, volumes, databases):

1. Show the user exactly what will be destroyed.
2. Ask for explicit confirmation.
3. Prefer updating an existing resource over deleting and recreating it.

## 3. Snapshot Before Surgery

Before modifying a database or critical config file:

1. Suggest a backup command to the user (e.g., `cp data.db data.db.bak`).
2. Wait for acknowledgement before proceeding.

## 4. Fail-Safe Communication

If unsure whether an action is destructive, STOP and ask. It is better to wait than to lose data.
