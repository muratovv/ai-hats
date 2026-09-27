# Changelog

All notable changes to `ai-hats-client` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres
to [Semantic Versioning](https://semver.org/).

## [0.2.0]

### Added

- The stub claude (`ai_hats_client.testing`) follows claude 2.1.283 further:
  - every prompt line gets `command_lifecycle` on the wire (`queued`, `started`,
    `completed`); a prompt folded into a running turn is echoed before it is
    started;
  - slash commands: `/model`, `/context`, `/usage` and `/rename` are answered by
    a `<synthetic>` response before their echo, and `/tui`, `/login` and the
    other refused ones get "isn't available in this environment" and no echo;
  - `/clear` writes `conversation_reset`, then goes on under a new session id
    and in a new transcript, and forgets the previous prompt.

## [0.1.0]

### Added

- `HeadlessSession`: start `ai-hats headless`, read its `headless/v1` header, send
  a `prompt`, wait for the `turn_ended` that carries the prompt's id, and close or
  abort the session. Every wait is bounded.
- `answer(call_id, decision, message=, answers=)` decides a question the
  session put; `interrupt()` stops the running turn and keeps the session.
- A turn wait takes `on_question`. A question stays offered until it is answered,
  its handler returns, or its call gets a result. With no handler the wait raises
  `QuestionPending` instead of waiting out its bound. `tool_call(call_id)` returns
  the call a question is about.
- A command the holder would refuse raises `ValueError` before it is sent; one the
  holder does not run (per `header.commands`), or any command after the session
  ended, raises `HeadlessError`.
- Leaving a `with` block terminates a holder that does not end within
  `close_timeout`, then raises the timeout.
- Typed: the wheel carries `py.typed`.
- `ai_hats_client.testing`: a stand-in `claude` binary that speaks the
  stream-json wire, so tests of a client run with no model and no login. It asks
  questions (`@ask`, `@askq`, `@plan`) and runs long turns an interrupt can cut
  (`@slow`, `@slowtool`). It drifts off the wire (`@drift`), hits the quota
  (`@quota <status>`, reset at `QUOTA_RESETS_AT`), dies by a signal (`@kill`,
  `@ignore-term`), and reports itself logged out under `LOGGED_OUT_ENV`.
