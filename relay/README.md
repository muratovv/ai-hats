# hats-relay

A session broker: **one server holds many `ai-hats` sessions and fans each one out to
many clients.** Start a session from a laptop, attach to it later from a phone, watch
the same session from both at once.

Developed inside the ai-hats repository but **deliberately not part of it** — this
package imports nothing from `ai_hats` and is not a member of its uv workspace. It is
meant to be lifted into its own repository by a `git subtree split` once the two sides
have been integrated. A test enforces the no-import rule so the boundary cannot rot.

## How it talks to a session

The broker never links against ai-hats; it spawns the `ai-hats` binary and speaks a
byte protocol over a socketpair, using the FD seam that ai-hats already exposes:

```
broker ──socketpair──> ai-hats (AI_HATS_PTY_IN_FD / AI_HATS_PTY_OUT_FD)
                          └── PTY ──> claude / cline / …
```

Session-side frames are `u8 type | u32 big-endian length | payload`, where type `0x00`
is opaque terminal bytes and `0x01` is a JSON control message such as
`{"resize": {"cols": 100, "rows": 30}}`.

Client-side, over WebSocket, the split follows the same idea: **binary messages carry
terminal bytes, text messages carry JSON control.** One WebSocket connection serves one
session; a client that wants two sessions opens two connections.

## Status

Early. LAN only: no TLS, no container, and **no authentication** — reaching the port is
the whole right to drive a session. Since a session is an AI agent with shell and
filesystem access, run this only on a network you trust, and always bind an explicit
address. Hardening is tracked separately.

## Development

```bash
python -m venv .venv
./.venv/bin/pip install -e '.[dev]'
./.venv/bin/python -m pytest tests -q
```
