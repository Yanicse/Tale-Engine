# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

- New campaigns keep the creations register and install the periodic planner. A pack may ship `chronology.json`,
  `clocks.json` and `arcs.json` to install the chronicler and the arcs registry; `novel validate` checks them and
  `novel example --world` installs a complete example. `novel campaign new` gains `--no-creations` and `--bounded-recovery`.
- `novel unblock` grants one more generation to an action whose two generations are spent, after a backup.
  `novel configure` switches the creations register and the bounded recovery of an existing campaign. `novel diagnose`
  shows how one attempt's context was selected.
- The manual engine no longer charges the action budget for writing its request: one wrong pasted answer no longer blocks
  the action for good.
- A Windows `claude.cmd` shim, which cannot carry the answer contract, is reported with its remedy.
- Tests ported: creations, bounded recovery, a twenty-action public session and a two-process lock.
- Magic uses a separate mana reserve and a mana bar. The engine spends mana atomically and refuses insufficient funds.
- Packs enable mechanical operations, configurable charges and cooldowns. Active abilities require a cost or explicit free use.
- Time no longer restores health. Recovery requires a sourced healing or mana recovery operation.
- Pack inventories and currencies support acquisitions, purchases and sales with explicit starting balances.
- Character descriptions reach the narrator; the bundled game kit verifier resolves its installed assets.
- New feature wording is English. Existing saves are not converted to the new mana mode automatically.

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
