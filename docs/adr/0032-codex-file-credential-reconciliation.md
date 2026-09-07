# ADR 0032: Reconcile Codex file credentials at session exit

Status: Proposed

## Context

The isolated Codex home previously projected auth.json as a symlink. Codex file
storage writes through it, but logout unlinks the link itself. The canonical
credential file survives and a new session loads it again. The behavior was
reproduced with Codex 0.149.0 and synthetic credentials.

## Decision

Stage auth.json as a private file with owner-only permissions, and store its
baseline digest in a private session artifact. Before session-home cleanup,
reconcile a changed file or its deletion against canonical credentials. Serialize
ai-hats reconciliation with a shared lock and only change canonical state if it
still matches the baseline. An unchanged session must never resurrect credentials
removed elsewhere. A conflict or I/O failure preserves the session home and reports
the failure. Crash-home recovery uses the same reconciliation.

An invalid baseline prevents reconciliation. A missing baseline retains the home
when a private credential copy is present, so incomplete staging or lost metadata
cannot silently discard it. Legacy homes containing only a credential symlink,
or no credentials, remain eligible for cleanup without reconciliation.

The materialization port supports private writes without recording contents or
digests in its public plan. Dry runs record the target without copying credentials
or creating lock files.

## Consequences

Synchronization occurs on session exit or crash-home recovery. Running sessions
keep their own snapshots; refreshes are not broadcast to other active sessions.
Standalone Codex does not honor the ai-hats lock, so the comparison protects
observed updates, not an external writer racing between comparison and replacement.
Keychain-backed credentials require a separate design and are not handled here.

Unconditional deletion of the symlink target was rejected because a newer login
may have replaced its credentials. Silently overwriting canonical state with the
last session to exit was rejected for the same reason.
