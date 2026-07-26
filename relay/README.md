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

## Driving a session from a browser

`--web` serves a self-contained xterm.js page from the port the broker already listens
on, so a device with nothing but a browser can drive a session:

```bash
hats-relay --host 127.0.0.1 --web --token "$(openssl rand -hex 16)"

# elsewhere: start a session, take the link, keep your shell
hats-relay-attach ws://127.0.0.1:8787 --role assistant --no-attach
# 880492ab3cace453c7b744be7dc32fca
# http://127.0.0.1:8787/?sid=880492ab…&token=…
```

The session belongs to the broker, not to the client that started it, so `--no-attach`
hands over a link and exits while the session keeps running.

**`--web` requires `--token`.** A browser always sends an `Origin`, and behind a tunnel
that origin is not knowable when the port is bound, so serving a page means accepting
any origin — the token is what stands in place of that check. It is compared on the
first control message of *every* op, `list` included, since the session ids `list`
returns are themselves the capability. Use `HATS_RELAY_TOKEN` to keep it out of `ps`.

`--provider` defaults to `claude` rather than to whatever the project resolves, because
the project default is `agy`, which is excluded from the remote channel on ToS grounds.
Pass `--provider ''` to take the project default anyway.

## Status

Early. LAN or a trusted tunnel: no TLS, no container, and **one shared token** — no
principals, no expiry, no per-session capabilities. Since a session is an AI agent with
shell and filesystem access, run this only where you trust the network, and always bind
an explicit address. Hardening is tracked separately.

## Development

```bash
python -m venv .venv
./.venv/bin/pip install -e '.[dev]'
./.venv/bin/python -m pytest tests -q
```
