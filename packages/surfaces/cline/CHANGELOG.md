# Changelog

All notable changes to `ai-hats-cline` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres
to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0]

`ClineParser` (Adapter B, HATS-960) — cline sessions now record structured turns
and token telemetry, not just a trace. Follows HATS-956, which shipped the
provider and descoped the parser.

### Added

- `ai_hats_cline.ClineParser` — a `TranscriptParser` that normalizes cline's
  single-object `<id>.messages.json` into observe's `ParsedTranscript` (turns +
  `model_stats` + `agg_usage`) and a full `usage/v1` report (always-on proxy,
  reconstructed-attribution timeline, tool aggregates). Wired via
  `ClineProvider.transcript_parser()` (HATS-948), replacing the default
  trace-only parse.

### Changed

- Depends on `ai-hats-observe>=0.3.0` (the `TranscriptParser` base + `usage/v1`
  schema `ClineParser` reuses) — the MVP omitted it.

## [0.1.0]

First cut of the Cline surface plugin (HATS-956) — the first in-tree consumer of
the provider IoC seam (HATS-870). Registers `cline` as an ai-hats provider via
the `ai_hats.providers` entry point, with zero edits to `src/ai_hats/**`.

### Added

- `ai_hats_cline.ClineProvider` — the `Provider` adapter for the `cline` CLI:
  inline-`-s` role delivery, interactive TUI for HITL (`cline -i`), headless
  `--yolo --json` for the automate path, no `--worktree`, ambient
  `CLINE_DATA_DIR` (keeps the machine's cline auth). Registered under the
  `ai_hats.providers` entry point as `cline`.
