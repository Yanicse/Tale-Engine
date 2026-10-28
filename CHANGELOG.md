# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

## 0.1.0

First public version.

### Added

- `novel` command: `doctor`, `init`, `ingest`, `canon plan|build`, `character new`, `validate`, `campaign new`, `example`,
  `play`, `resume`, `read`, `routine`.
- Canon build from `.txt`, `.md`, `.epub` and `.pdf` books: resumable, estimated before sending, quotes checked against
  the text, entities merged, volumes closed.
- Four engines for the canon and for playing: Claude CLI, Codex CLI, Anthropic API, manual copy and paste.
- Novel packs (`novel.json`, `character.json`, `theme.json`) with `tracked_npcs`, `currency_rates`, `world_rules`,
  an opening scene, abilities with costs and forbidden resources, and strict validation with `novel validate`.
- Reading page: five themes, colors, icons by category, English and French texts, per-novel labels, engine selector.
- Bundled example novel, *The Salt Lantern*, installable without an account.
- Prompt texts as files under `novel_engine/prompts/en`, with an English fallback for translations.
- Documentation under `docs/`, checked by tests.
- CI on Linux, macOS and Windows with Python 3.10 and 3.12, plus an installed-package smoke test.

### Known limits

- Charges, cooldowns and day and night rules exist in the engine but a pack cannot enable them yet.
- The narrator's instruction texts are English only.
