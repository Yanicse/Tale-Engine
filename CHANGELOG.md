# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

- The French reading page translates the cooldown and mana cost lines, and the quantity, precision and place of a
  possession, which were shown in English.
- `novel play --topics` and `/topics` list the memory topics the character can look up. The option was called
  `--files` and described as a list of files; `--files` still works. The `--json` status of this view is now `topics`.
- New campaigns turn the canon selection on: the narrator receives the sourced claims that match each turn and may
  cite them as causes. It was off, so the canon never reached the narrator. `novel configure --canon-selection on`
  turns it on for an existing campaign.
- New campaigns keep the creations register and install the periodic planner. A pack may ship `chronology.json`,
  `clocks.json` and `arcs.json` to install the chronicler and the arcs registry; `novel validate` checks them and
  `novel example --world` installs a complete example. `novel campaign new` gains `--no-creations` and `--bounded-recovery`.
- `novel unblock` grants one more generation to an action whose two generations are spent, after a backup.
  `novel configure` switches the creations register and the bounded recovery of an existing campaign. `novel diagnose`
  shows how one attempt's context was selected.
- The manual engine no longer charges the action budget for writing its request: one wrong pasted answer no longer blocks
  the action for good.
  Running the command again on an unchanged refused answer no longer spends a second generation, and the wait for an
  answer is reported as `Waiting for a manual answer` instead of an interruption.
- A Windows `claude.cmd` shim, which cannot carry the answer contract, is reported with its remedy.
- Tests ported: creations, bounded recovery, a twenty-action public session and a two-process lock.
- Magic uses a separate mana reserve and a mana bar. The engine spends mana atomically and refuses insufficient funds.
- Packs enable mechanical operations, configurable charges and cooldowns. Active abilities require a cost or explicit free use.
- Time no longer restores health. Recovery requires a sourced healing or mana recovery operation.
- Pack inventories and currencies support acquisitions, purchases and sales with explicit starting balances.
- Character descriptions reach the narrator; the bundled game kit verifier resolves its installed assets.
- Leftover French and word-for-word wording is corrected in messages (validation errors, insufficient balance or stock,
  diagnostics, the reading server). The `--json` statuses `required_memory`, `stop_technique` and the planner status
  `non_required` are now `memory_required`, `technical_stop` and `not_required`. Passives show as `passive` in `--skills`.
- The arc decision names `a_prepare` and `fact_world` are now `to_prepare` and `world_fact`; arcs saved with the old
  status still read correctly. The canon selection looks for spells and coins instead of the French `sort` and `pieces`.
- New feature wording is English. Existing saves are not converted to the new mana mode automatically.
- With installed mechanics, the narrator's instructions announce the envelope version 1.3 that the answer schema
  requires; they still said 1.2 because the switch looked for the French sentence.
- The `novel` commands write UTF-8. On Windows, a redirected output used the ANSI code page, so `novel unblock`,
  `novel canon search` and the other direct commands failed on characters it cannot encode.
- The reading page header translates its exchange count, version and pending action chips (`header.*` keys); it showed
  `action en waiting` in every language.
- An object without a figure is refused as a `Null quantity (not counted)` when its precision is not `unknown`; the
  message said `Zero quantity`, which told the model that an uncounted quantity was zero.
- The planner's chronicle packet names `recent_manifestations` and `limits.manifestations_per_review` instead of the
  French `manifestations_recentes` and `manifestations_par_revision`; `novel routine correct` answers with `next`
  instead of `suite`.
- Routines call their folder a folder: `novel routine status` answers with `folder` instead of `file`, and the messages
  no longer speak of a "file". The default topics and delegation fit any novel; they spoke of levies, military movements
  and war.
- `novel play --help` and the other game commands show `usage: novel play` instead of the script file name, and the
  ability prompt of `novel character new` shows `%` instead of `%%`.
- `novel resume --enable campaigns/<slug>/campaign.sqlite` finds the save from any folder (it was read from the current
  folder, not `NOVEL_HOME`), reports a refused path without a traceback, and is documented.
- The reading page shows an empty Creations tab on a new campaign instead of "Creative format inactive".
- The documentation no longer says that a `corpus/` folder is created. `campaign_cli` reads the active campaign by
  default, except `init` and `action-demo` which keep the fictional demo save.
- The prompts and agent rules lose the last traces of the original campaign: a dated rule number, an `accord` key in
  the path examples, conscription words in the routine guidance, "the Codex loop", "a narrator running in the chat",
  and "never cooldown an effect by reopening", which now says that reopening or resuming never refills a charge or
  ends a cooldown. Memory and agenda texts say topic and matter instead of "file".
- Messages left in French or in French word order are corrected (`Ressource`, `Ponctuation`, `Lien narrative`,
  `Manifestation en double`, `Version narrative unknown`, `Base existing required` and others), and `/creations` is
  listed in the interactive commands.
- The default memory topics no longer include a guild exam, a building site and a lodging taken from the original
  campaign; only the confirmed powers topic remains, and the narrator adds a campaign's own topics. The canon
  selection word lists lose their French words and duplicates.

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
