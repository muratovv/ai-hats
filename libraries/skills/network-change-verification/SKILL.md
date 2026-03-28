# Network Change Verification

Pre/post verification checklist for network and routing changes.

## Before Change
1. **Source Check**: Verify external DNS/Domain points to the correct IP.
2. **Path Check**: Verify tunnels (WireGuard, etc.) are active.
3. **Destination Check**: Run `curl` or `nc` on the target to confirm the service is listening.

## After Change
1. **Summarize flow**: Document the new traffic path (e.g., `User -> HTTPS (443) -> Traefik -> Tunnel -> Service (8080)`).
2. **Verify end-to-end**: Confirm the full path works from the external entry point.
3. **Update docs**: Ask the user to verify the documentation update.
