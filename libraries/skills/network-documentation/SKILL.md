# Network Documentation

Maintain the network source of truth throughout SRE tasks.

## Procedure
1. **Locate index**: Find the project's infrastructure index (e.g., `INFRASTRUCTURE.md`).
2. **Update mapping**: Maintain `Domain -> Internal IP:Port -> Tunnel/Gateway` table after every topology change.
3. **Record rationale**: Document WHY a specific port, bridge, or route was chosen.
4. **Verify accuracy**: Cross-check the documented state against the live state periodically.
