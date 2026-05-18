---
name: gworkspace-cli
description: Google Workspace via the gws CLI (Drive/Sheets/Docs/Gmail/Calendar/Chat/Admin)
---
# Google Workspace CLI (gws)

Single-command access to Google Workspace APIs through one `gws` binary on `$PATH`. Use when the agent needs to read or write any Workspace data: Drive files, Sheets values, Docs content, Gmail messages, Calendar events, Chat messages, or Admin SDK objects.

## Setup

If `gws` is not installed, or `gws auth status` reports no active credentials — open [`docs/integrations/gworkspace-cli-setup.md`](../../../../docs/integrations/gworkspace-cli-setup.md) and walk the user through install, auth, and permission-allowlist steps. Do not try to install or authenticate without surfacing the guide.

## Top-level command groups

| Group      | Purpose                                            |
|------------|----------------------------------------------------|
| `drive`    | Files, folders, sharing, copy/move/trash           |
| `sheets`   | Spreadsheets, values, batch updates                |
| `docs`     | Documents, structured content edits                |
| `gmail`    | Threads, messages, labels, send                    |
| `calendar` | Calendars, events, agenda                          |
| `chat`     | Spaces, messages                                   |
| `admin`    | Users, groups, org units (Workspace admin only)    |

## Discovery

Commands are generated dynamically from Google's Discovery Service plus hand-crafted helpers (prefixed `+`):

```bash
gws --help                    # all groups
gws <group> --help            # methods in a group
gws <group> <method> --help   # params for a single method
```

When unsure which method fits — start from `gws <group> --help`, then narrow.

## Common patterns

Read a range:
```bash
gws sheets spreadsheets values get \
  --params '{"spreadsheetId":"<id>","range":"Sheet1!A:Z"}' \
  --format csv
```

Append a row (dates and formulas parsed):
```bash
gws sheets spreadsheets values append \
  --params '{"spreadsheetId":"<id>","range":"Sheet1!A1","valueInputOption":"USER_ENTERED"}' \
  --json '{"values":[["2026-05-18","value"]]}'
```

Find a file by name:
```bash
gws drive files list \
  --params '{"q":"name = '\''Report'\'' and trashed = false","fields":"files(id,name)"}'
```

## Safety & conventions

- **No secrets in code or examples.** Auth state lives in `~/.config/gws/` (AES-256-GCM at rest); never embed tokens, client secrets, or service-account keys.
- **Identifiers via env, not hardcode.** Spreadsheet IDs, folder IDs, etc. — read from environment or config files, not literal strings.
- **`USER_ENTERED` for values that contain dates, numbers, or formulas; `RAW` only for verbatim strings.**
- **Append-only for journals.** Use `values append` (not `update`) for transaction logs; never overwrite history.
- **Snapshot before destructive writes.** `gws drive files copy` the spreadsheet/doc before bulk writes to critical sheets.
- **CLI resolves via `$PATH`.** No hardcoded install paths; if `gws` is missing, surface the setup guide.

## Anti-patterns

- Hardcoding spreadsheet/folder IDs in scripts or skill content.
- Writing tokens or OAuth secrets into the repo or skill files.
- Using `RAW` when the values include dates or formulas — they end up as plain strings.
- Overwriting an append-only journal sheet with `values update`.
- Calling `gws` from a fully qualified path (`/usr/local/bin/gws`) — break portability.
- Bulk writes without a prior `gws drive files copy` snapshot.
