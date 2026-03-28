# Network Documentation & Contracts

## 1. Network Source of Truth
Any network topology change MUST be documented immediately in the project's infrastructure index.
- **Table Format**: Maintain `Domain -> Internal IP:Port -> Tunnel/Gateway` mapping.
- **History**: Record WHY a specific port or bridge was chosen.

## 2. Pre-change Verification
Before applying a routing change:
1. **Source Check**: Verify external DNS/Domain points to the correct IP.
2. **Path Check**: Verify tunnels (WireGuard, etc.) are active.
3. **Destination Check**: Run `curl` or `nc` on the target to confirm the service is listening.

## 3. Post-change Audit
After a successful network change:
- Provide a summary of the new traffic flow (e.g., `User -> HTTPS (443) -> Traefik -> Tunnel -> Service (8080)`).
- Ask the user to verify the documentation update.

## 4. No Ghost Ports
If a service is moved or deleted, its firewall rules and proxy routes MUST be cleaned up simultaneously.
