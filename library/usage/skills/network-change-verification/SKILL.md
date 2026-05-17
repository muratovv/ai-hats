---
name: network-change-verification
description: Pre/post verification checklist for network and routing changes
---
# Network Change Verification

Pre/post verification checklist for network and routing changes.

## When to Use
- Before and after any DNS, routing, tunnel, or proxy change
- When exposing a new service externally
- Troubleshooting connectivity issues

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
