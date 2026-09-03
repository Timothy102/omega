# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [0.3.0] - 2026-09-03

The v2 rebuild: a from-scratch harness architecture, a Textual TUI, and the
project's rename from `rig` to `omega`.

### Added
- Harness v2: event pipeline, artifact offload, memory graph, `ask_user`, and a plain-text UI fallback (9e4b9f6)
- Textual TUI, built up over several passes: core UI (d84f86a), thinking state, onboarding wizard, honest tool-call lines, sidebar rework (cc805cd)
- Native Anthropic backend alongside OpenAI, with a model catalog and picker, plus onboarding (c5ca438)
- Connections manager: integration catalog and lazy MCP connect (823aeb7)
- Git tab: repository discovery and recent commits via `gitlog` (de6f9d2)
- Context engineering, reliability improvements, an eval harness with example tasks, and bundled skills (7764a4e)

### Changed
- Renamed the project from `rig` to `omega` (ae35441)
- Stricter typing, shared UI formatting helpers, stdin prompt support (639f00a)
- CLI now warns when a subcommand ignores trailing flags (29b3309)
- Point a new user at `omega setup` when no API key is configured (e17fd8f)

### Fixed
- Stopped conflating long-term memory with the conversation (848ae6f)

### Documentation
- README and packaging cleanup: real clone URL, MIT licence, packaging ignores (46c87d0, 802c0e7)
