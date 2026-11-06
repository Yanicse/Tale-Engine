# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

Instruction texts and rules changed in this release (the narrator, common, possession, chronicler, planner, routine, agenda, referee, off-screen and archivist rules, and the narrator prompts). A campaign that is already open asks for an explicit restart. Existing saves are not converted to the new mana mode automatically.

### Added

- Magic uses a separate mana reserve and a mana bar. The engine spends mana atomically and refuses insufficient funds. Packs enable mechanical operations, configurable charges and cooldowns; active abilities require a cost or an explicit free use. Time no longer restores health: recovery requires a sourced healing or mana recovery operation.
- The new mechanical operation `raise_max` lets the maximum HP and mana of a character grow in play (a level-up): `ability_id` is `hp` or `mana`, `amount` is the new maximum, which must be above the current one. It never heals or refills and is refused after a death. With the installed mechanics a maximum can no longer be frozen only by the sheet: it never decreases and rises only through this operation; free changes of a maximum stay refused, and campaigns without mechanics keep their maximums frozen.
- `costs.fixed_cost_points` in a character sheet gives an ability a cost in mana points that the engine charges exactly at every use, independent of the maximum mana and of the power (an ability has one cost, percent or fixed). `novel validate` checks it, and the ability views and the computed cost table show it.
- Pack inventories and currencies support acquisitions, purchases and sales with explicit starting balances. Character descriptions reach the narrator, and the bundled game kit verifier resolves its installed assets.
- New campaigns keep the creations register, install the periodic planner and turn the canon selection on: the narrator receives the sourced claims that match each turn and may cite them as causes. A pack may ship `chronology.json`, `clocks.json` and `arcs.json` to install the chronicler and the arcs registry; `novel validate` checks them and `novel example --world` installs a complete example.
- `novel campaign new` gains `--no-creations` and `--bounded-recovery`. `novel configure` switches the creations register, the bounded recovery and the canon selection (`--canon-selection on`) of an existing campaign. `novel unblock` grants one more generation to an action whose two generations are spent, after a backup. `novel diagnose` shows how one attempt's context was selected.
- `novel play --topics` and `/topics` list the memory topics the character can look up (the option was called `--files`, which still works; the `--json` status of this view is `topics`). `/creations` is listed in the interactive commands.
- `novel resume --enable campaigns/<slug>/campaign.sqlite` finds the save from any folder (it was read from the current folder, not `NOVEL_HOME`), reports a refused path without a traceback, and is documented.
- The reading page translates the statuses and forms it used to show as raw values of the save ("in_progress", "planned", "always", "passive"): project, agenda, creation and NPC statuses and the form of an ability have `status.*` and `form.*` texts in the English and French packs (about 140 page texts now). It also translates the cooldown and mana cost lines and the quantity, precision and place of a possession in French.
- The command reference documents every option of `novel play` (`--campaign`, `--search`, `--planner-effort`, `--planner-delay`, `--codex`, `--claude`). Every `novel` subcommand and option has a help text that says what it does, and `novel --help` lists `.md` among the book formats `novel ingest` accepts.
- A Windows `claude.cmd` shim, which cannot carry the answer contract, is reported with its remedy.
- Tests ported: creations, bounded recovery, a twenty-action public session and a two-process lock. New checks guard the wording of the code, prompts, rules, comments, test data, test names, the reading page source and the agreement of nouns with numbers.

### Changed

- Messages, comments, prompts and rules read as English instead of word-for-word French. The main families: "acquired", "accomplished", "designate", "proof" and "dosage" became "established", "completed", "point to", "evidence" and "power"; "serve to", "waiting formula", "notably", "imposes", "modalities" and "evolution" became plain equivalents; fifteen "nor" after a negation became "or"; "Incorrect ..." became "Invalid ..."; "the manager", "arbitrations" and "the historical demo" became the engine, rulings and the legacy demo; the page is the "reading page" everywhere. An NPC "does not need to" personally guarantee a testimony (the prompt said "must not", the opposite of the narrator rules).
- Some public keys and values have English names: the `--json` statuses `required_memory`, `stop_technique` and `non_required` are `memory_required`, `technical_stop` and `not_required`; the planner packet names `recent_manifestations` and `limits.manifestations_per_review`; passives show as `passive` in `--skills`; `novel routine correct` answers with `next` and `novel routine status` with `folder`; the arc decision names `a_prepare` and `fact_world` are `to_prepare` and `world_fact` (saves with the old status still read correctly) and its key `motif` is `kind` (an old answer or arc is read as `kind`); `novel diagnose` names an omitted block `block`; the planning reduction of the targeted context is `planning`; the demo rule `adaptation:incertitude` is `adaptation:uncertainty` (existing demo campaigns keep the identifier they saved).
- The scene pacing refusal names the configured limit ("The limit of 4 replies requires the requested encounter to take place.") instead of always saying "three-reply"; its packet keys are `replies_since_request`, `reply_number` and `encounter_due_minute`, and the instruction says "the deadline is the moment the encounter must happen". The agenda helper `_routine_reportee` (French for "postponed") is `_is_postponed_follow_up`.
- Messages agree in number ("1 batch", "1 page extracted", "1 claim", "1 warning", "Routine of 1 day", "Limit: 1 second.", "You wait 1 minute."; `novel_engine/wording.py` has the helper) and the "(s)" forms are gone. The reading page counters need no plural: "Exchanges: 2", "Results for “x”: 1", "NPCs: 1 of 3", and the French ones follow. The English page asks "What does {name} do?", says "Mood is fixed" for a forced theme and writes "Default power: 10%." without the French space.
- Smaller wording fixes: the 6 pm routine checkpoints are "checkpoints" (not "points") in the option help and the bulletin rule; the French interval notation `]start, end]` is `(start, end]`; `--legacy-mode` says "Previous behavior"; `novel example` and `novel campaign new` print "World setup: summaries." (or "none"); `novel play --help` is plain English, shows `usage: novel play` instead of the script file name, and says the delays are in seconds; the ability prompt of `novel character new` shows `%` instead of `%%`; `novel validate` says "must be text" and "is not a page text".
- The reading page code uses English names: `titles`, `gold`, `coin`, `purse-label`, `purse-amount`, `paragraphs`, `searchStory` (it searches the story, not the NPCs), `query`, `controls`, `purse-card`, `dust`, `masthead`, a `busy` class on the footer and the `reader-abilities-filter` browser key (the saved ability filter is reset once). Its comments are reworded and a dead condition of the ability filter is gone.
- The manual engine no longer charges the action budget for writing its request, so one wrong pasted answer no longer blocks the action for good. Running the command again on an unchanged refused answer no longer spends a second generation, and the wait for an answer is reported as `Waiting for a manual answer` instead of an interruption.
- The em dash (U+2014) is an ordinary character again: the narrator and planner instructions no longer forbid it, a response that contains one is kept as written instead of being refused or rewritten, and canon excerpts and error messages keep it too.
- The default memory topics and the routine delegation fit any novel (no guild exam, building site, lodging, levies, military movements or war); only the confirmed powers topic remains, and the narrator adds a campaign's own topics. The canon selection word lists lose their French words and duplicates (`kingdom`, `theocratie`), and it looks for spells and coins instead of the French `sort` and `pieces`.
- Documentation: the pages no longer use "the engine" for two things in one sentence, say "change them with `--model`", "is neither read nor charged again" and "possessions, money and mana costs", state that `novel canon build` defaults to the manual engine, open the reading page documentation with `novel read`, count about 140 page texts and explain `LANGUAGE_NAMES`, and name `novel play --inventory --json` instead of the internal `play_narrative.py` script. The `corpus/` folder is no longer said to be created, and `campaign_cli` reads the active campaign by default, except `init` and `action-demo`.
- The bundled story *The Salt Lantern* agrees with itself: the inspector arrives "the morning after the lantern was stolen", the keeper climbs the stairs "after nightfall to trim the wick" (the lantern was already burning when it went dark), the money is labeled "gold crowns" and "silver gulls" like in the text and the canon, and the clocks belong to "Corwen Pike" and "the Tidewrights".
- The test data is English again (about thirty French words translated, "travelling" is "traveling"), test names and docstrings follow the wording of the code ("evidence", "established", `ReaderTests`), and two code lists no longer repeat an item. The terminal demo test sends the documented `quit` command instead of the French `quitter`.

### Fixed

- The reading page gives the form badge of an ability its gold style again (the class was still `or` after the CSS was renamed), uses a tower icon for places named like a tower (the rule said `turn`, from the French "tour"), names the particle blend key `additive` instead of `clair`, and shows an empty Creations tab on a new campaign instead of "Creative format inactive".
- The reading page header translates its exchange count, version and pending action chips (`header.*` keys); it showed `action en waiting` in every language.
- The `novel` commands write UTF-8: on Windows, a redirected output used the ANSI code page, so `novel unblock`, `novel canon search` and the other direct commands failed on characters it cannot encode.
- With installed mechanics, the narrator's instructions announce the envelope version 1.3 that the answer schema requires; they still said 1.2 because the switch looked for the French sentence.
- An object without a figure is refused as a `Null quantity (not counted)` when its precision is not `unknown`; the message said `Zero quantity`, which told the model that an uncounted quantity was zero.
- The common rules say that a missing knowledge attribution does not mean an NPC does not know a fact (a calque of "ignorer"), and that reopening or resuming never refills a charge or ends a cooldown. The possessions rules say "buying what is still missing within the available funds" instead of "buying the balance".

### Removed

- Leftovers of the original project: the search in the text of the book (`CanonRepository.search_sources` and the `--sources` option of `search-canon` read a `corpus/library.sqlite` that nothing ever created), the narrator document filter for character sheet headings that no shipped document has, dated comments and references to volumes, the former `vitality-1.0` magic mode and a hunger rule in the documentation, the search scripts of the original archivist rules, and the `sheet_*` predicate bonus of the canon selection, which came from the character sheets of one novel. Prompts and rules lose a dated rule number, an `accord` key in path examples, conscription words, "the Codex loop" and "a narrator running in the chat".

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
