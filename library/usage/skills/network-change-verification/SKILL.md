---
name: network-change-verification
description: Pre/post verification checklist for network and routing changes. Use before and after any DNS, routing, tunnel, or proxy change, when exposing a new service externally, or when troubleshooting connectivity issues.
---
# Network Change Verification

Pre/post verification checklist for network and routing changes.

## When to Use
**Verifying connectivity around a change** — the before/after probes for a DNS,
route, tunnel, or proxy edit. Its sibling **network-documentation** owns the
*record* of the resulting topology: verify the change works here, then update the
source-of-truth there. Verification is the test; documentation is the artifact.

## Before Change
1. **Source Check**: Verify external DNS/Domain points to the correct IP.
2. **Path Check**: Verify tunnels (WireGuard, etc.) are active.
3. **Destination Check**: Run `curl` or `nc` on the target to confirm the service is listening.

## After Change
1. **Summarize flow**: Document the new traffic path (e.g., `User → HTTPS (443) → Traefik → Tunnel → Service (8080)`).
2. **Verify end-to-end**: Confirm the full path works from the external entry point.
3. **Update docs**: Ask the user to verify the documentation update.

## Completion
- Pre-change baseline captured
- Post-change end-to-end connectivity verified
- Traffic path documented and docs updated

## Anti-Patterns
- Skipping pre-change baseline — can't tell if the change broke something or it was already broken
- Verifying only internal hops — must test from the external entry point
- Forgetting doc update — next person won't know the new path
