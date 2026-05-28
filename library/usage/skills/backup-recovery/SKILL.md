---
name: backup-recovery
description: 3-2-1 backup strategy with verified restore procedures. Use when setting up or reviewing backup infrastructure, before risky data migrations or destructive operations, or after adding new persistent storage such as a database or volume.
---
# Backup & Recovery

Ensure data safety through verified backup strategy.

## When to Use
- Setting up or reviewing backup infrastructure
- Before risky data migrations or destructive operations
- After adding new persistent storage (DB, volume)

## Procedure
1. **Identify targets**: List all persistent data (DBs, volumes, configs).
2. **Apply 3-2-1 rule**: 3 copies, 2 different media, 1 offsite.
3. **Automate**: Every DB or persistent volume must have an automated backup job.
4. **Verify restore**: A backup is not valid until a restore has been tested. Run periodic restore drills.
5. **Document**: Record backup schedule, retention policy, and restore procedure in project docs.

## Completion
- All persistent data has automated backup jobs
- Restore tested at least once
- Schedule, retention, and restore steps documented

## Anti-Patterns
- Untested backups — a backup that can't restore is worthless
- Manual-only backups — will be forgotten and skipped
- No offsite copy — local disaster destroys all copies
