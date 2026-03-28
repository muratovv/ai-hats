# Backup & Recovery

Ensure data safety through verified backup strategy.

## Procedure
1. **Identify targets**: List all persistent data (DBs, volumes, configs).
2. **Apply 3-2-1 rule**: 3 copies, 2 different media, 1 offsite.
3. **Automate**: Every DB or persistent volume must have an automated backup job.
4. **Verify restore**: A backup is not valid until a restore has been tested. Run periodic restore drills.
5. **Document**: Record backup schedule, retention policy, and restore procedure in project docs.
