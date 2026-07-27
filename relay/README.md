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

**`--token` is required — there is no unauthenticated mode.** A session is an agent with
a shell, so reaching the port must not be the whole right to drive it. The token is
compared on the first control message of *every* op, `list` included, since the session
ids `list` returns are themselves the capability. Use `HATS_RELAY_TOKEN` to keep it out
of `ps`. `--web` additionally means accepting any `Origin` (a browser always sends one,
and behind a tunnel it is not knowable when the port is bound), which is the second
reason the token is not optional.

`--provider` is empty by default: the session is whatever the project resolves, and the
relay does not second-guess it. Antigravity (`agy`) is fine here — the official binary
runs locally under its own login and we relay its terminal, the shape its own CLI
supports over SSH. What is *not* fine is unattended agy on consumer credentials; see
`HATS-1232` for the reasoning and its guardrails.

## Status

Early. LAN or a trusted tunnel: no TLS, no container, and **one shared token** — no
principals, no expiry, no per-session capabilities. Since a session is an AI agent with
shell and filesystem access, run this only where you trust the network, and always bind
an explicit address. Hardening is tracked separately.

## Development

```bash
python -m venv .venv
./.venv/bin/pip install -e '.[dev]'
make test
```

### Running locally

#### Server Management

- **Start server**:
  ```bash
  make relay-server                           # from repo root (or `make run-server` inside relay/)
  make relay-server ARGS="--port 8787 -v"     # with custom options
  ```
  By default, the server binds to `127.0.0.1:8787`.

- **Stop server**:
  Press `Ctrl-C` (or send `SIGINT` / `SIGTERM`). The server catches the signal, cleanly terminates active sessions, and shuts down.

#### Client & Session Management

- **Create a new client session**:
  ```bash
  make relay-client                           # creates a new session with default role ('assistant')
  make relay-client ROLE=reviewer             # creates a new session with a specific role
  ```

- **List active sessions**:
  ```bash
  make relay-client ARGS="--list"
  ```

- **Attach to an existing session**:
  ```bash
  make relay-client ARGS="--sid <SID>"
  ```

- **Detach from a session**:
  Press `F12` (or your configured `--detach-key`). The client restores local terminal settings and detaches, leaving the session running asynchronously on the server.

