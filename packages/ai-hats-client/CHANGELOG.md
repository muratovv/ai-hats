# Changelog

All notable changes to `ai-hats-client` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres
to [Semantic Versioning](https://semver.org/).

## [0.1.0]

### Added

- `HeadlessSession`: start `ai-hats headless`, read its `headless/v1` header, send
  a `prompt`, wait for the `turn_ended` that carries the prompt's id, and close or
  abort the session. Every wait is bounded.
- `ai_hats_client.testing`: a stand-in `claude` binary that speaks the
  stream-json wire, so tests of a client run with no model and no login.
