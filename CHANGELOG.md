# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

- The reading page code uses English names where it kept French-shaped ones: `searchStory` (it searches the story, not the NPCs), `query` instead of `request`, the `controls` row instead of `rank`, `purse-card` instead of `piece`, `dust` instead of `dust2`, `masthead` instead of `markShape`, a `busy` class on the footer, and the `reader-abilities-filter` browser key (the saved ability filter is reset once). Four comments are reworded ("follows its arc", "point to", "keywords", "panel depth") and a dead condition of the ability filter is removed.
- The reading page gives the form badge of an ability its gold style again (the class was still `or` after the CSS was renamed), uses a tower icon for places named like a tower (the rule said `turn`, from the French "tour") and names the particle blend key `additive` instead of `clair`. `tests/test_reader_page_source.py` checks that every badge class the script uses has a style rule.
- The narrator, common and possession rules say "are used to prepare", "is for navigation", "a stock closing phrase" and "in particular" instead of "serve to", "serves to", "a waiting formula" and "notably". The paragraph signatures of `instructions.py` follow. A campaign already open asks for an explicit restart because the narrator document changed.
- The narrator prompts and the chronicler, planner and routine rules read as English: an NPC "does not need to" personally guarantee a testimony (the prompt said "must not", the opposite of the narrator rules), "reserved for", "matter" instead of "file" for a closed agreement, "tracked commitments", "details" instead of "modalities", "requires" instead of "imposes", "by contrast", "in particular", "situational", "is used only for" and "exist for the validations". A campaign already open asks for an explicit restart because the instruction texts changed.
- The arc decision key `motif` (the kind of move: progression, obstacle, dependency or divergence) is now `kind`, in the planner schema, the arc checks and the chronicler rules. A planner answer that still uses `motif`, and an arc saved with `motif` in its last decision, are read as `kind`. A campaign already open asks for an explicit restart because the chronicler document changed.
- Leftovers of the original project are gone from the code. A comment no longer says "the scenes of volumes 6 and 8" and two comments no longer carry the date of a measurement. The search in the text of the book is removed: `CanonRepository.search_sources` and the `--sources` option of `search-canon` read a `corpus/library.sqlite` that nothing ever created. The narrator document filter no longer looks for the headings of a character sheet document ("Technical scope", "Common costs and dosage") that no shipped document has.
- Smaller wording fixes: "Invalid topic identifier", "Invalid memo identifier", "Invalid lot identifier" and "Invalid currency or payment sign" replace "Incorrect ...", the schema check says "value differs from the required constant", the planner names `old_synthesis` instead of "An old synthesis", and the page is the "reading page" everywhere (the credits, the page texts module and the documentation titles said "reader page"). The package classifiers and the CI matrix both list Python 3.11, and a test keeps them equal.
- Two code lists no longer repeat an item: the paragraph prefix `In envelope 1.2,` in the narrator document filter and `actor` and `title` in
  the project fields of the planner canon query; `tests/test_migration_wording.py` fails when a literal list of strings repeats one.
- The common rules, the possession rules and the narrator rules use "evidence", "established" and "completed" instead of "proof", "acquired" and "accomplished", say "Consequences never invent choices or thoughts for the player", "leave it unknown", "construction project" and "a specific quantity", and "marks" instead of "designates". The paragraph signatures of `instructions.py` follow. A campaign already open asks for an explicit restart because the narrator document changed.
- "acquired", "accomplished", "designate" and "proof" were used for the French acquis, accompli, designer and preuve in the agenda, arc, possession and memory messages, the chronicler, planner and archivist rules and the narrator prompts: they now say "established", "completed", "point to" and "evidence". The `proofs` keys of the packets keep their name. A campaign already open asks for an explicit restart because the instruction texts changed.
- The documentation no longer uses "the engine" for two things in one sentence ("Tale Engine writes the request" for the manual engine), says "change them with `--model`", "is neither read nor charged again" and "possessions, money and mana costs", and the README states that `novel canon build` defaults to the manual engine.
- The ability lookup says "power" where it said "dosage" (a calque), like the character sheets page.
- Messages and comments read as English: "Re-evaluation is reserved for a rejected action", "An action is still pending; resume it before starting another", "Cannot delete a value that does not exist", "The blow is not lethal or exceeds ...", "A started arc cannot go back to the preparation stage", "Invalid editorial exclusion", "Positions establish a location", the daily bulletin rule of routines, the memory entity reminder, and the "or ask" that followed "Do not invent". `tests/test_migration_wording.py` now also scans the comments of the code.
- The test data is English again: about thirty French words left in the tests ("Visite", "Attendre", "Guilde", "Pouvoir public",
  `corps`, `casse` and the like) are translated, "travelling" is "traveling", and `tests/test_test_data_language.py` fails when
  one comes back, except in the tests that exercise French on purpose.
- The terminal demo test sends the documented `quit` command instead of the French `quitter`, which the demo took for an
  action and only ended on the end of input; it now checks that quitting records nothing.
- The bundled story *The Salt Lantern* says the inspector arrives "the morning after the lantern was stolen", not "on the very
  day", since the lantern goes dark the evening before.
- The narrator, chronicler, planner and agenda rules lose fifteen "nor" that followed "without", "do not" or "no" (it is
  now "or"), two verbs that did not agree after "or" ("stays what it is", "a creation or a postponement provides"),
  "give no knowledge ... automatically" in the canon rule, "a lot of elapsed time" and the capital letter of the identifier
  `Descriptive_marker` that opened a sentence. A campaign already open asks for an explicit restart because the narrator
  document changed.
- Messages read as English: "SQLite integrity check failed" replaces "Incorrect SQLite integrity" and "incorrect integrity" in the
  maintenance and installation scripts, `novel validate` says "must be text" and "is not a page text" instead of "must be a
  text", the maintenance preview says the application compatibility is checked "when it is applied", and an arc
  dependency must be "truly open".
- The English reading page asks "What does {name} do?" instead of "What {name} does...", counts NPCs as "{n} of {total} NPCs",
  says "Mood is fixed" for a forced theme, and writes "Default power: 10%." without the French space before the sign.
- The character sheets and troubleshooting pages no longer describe the former `vitality-1.0` magic mode, which
  only served the original campaign.
- The canon selection no longer gives a bonus to the `sheet_*` predicates (level, karma, alignment and the like),
  which came from the character sheets of one novel and are never produced by `novel canon`.
- The em dash (U+2014) is an ordinary character again: the narrator and planner instructions no longer forbid it,
  a response that contains one is kept as written instead of being refused or rewritten, and canon excerpts and
  error messages keep it too.
- `novel diagnose` names an omitted block `block` instead of `bloc`, and the planning reduction of the targeted
  context is called `planning` instead of `planification`.
- The reading page code uses English class and function names (`titles`, `gold`, `coin`, `purse-label`,
  `purse-amount`, `paragraphs`) instead of French ones.
- The canon selection no longer lists `kingdom` twice and the French `theocratie` among the words that mark a state.
- The demo adaptation rule `adaptation:incertitude` is now `adaptation:uncertainty`; demo campaigns already created
  keep the identifier they saved.
- The JSON contracts, scenarios and examples are reworded: "Proposition contract" is "Proposal contract", and the
  scenarios and demo examples lose "nor", "display as acquired" and "a surveillance of the gates during reading".
- The referee, off-screen and archivist rules are reworded: no more "the missing arbitration", "a certain ability",
  "stop the useful work", "the manager" or "nor" after a negation.
- The common rules sent to the narrator lose their remaining calques: "the manager" is the engine, "arbitrations"
  are rulings, "the historical demo" is the legacy demo, and "personal knowledge of the work" is your own knowledge
  of the book.
- The languages page counts about 120 page texts, not 100, and explains `LANGUAGE_NAMES` plainly.
- The reader page documentation opens with `novel read`; the sentence on the Abilities tab moved to "What it shows".
- The command reference documents every option of `novel play`, adding `--campaign`, `--search`,
  `--planner-effort`, `--planner-delay`, `--codex` and `--claude`, and calls the local views lookups.
- `novel --help` lists `.md` among the book formats `novel ingest` accepts.
- The descriptions of `novel diagnose` and `novel read` and the error of a lookup given with an action no longer
  use the calques "technical consultation" and "official path".
- Every `novel` subcommand and option now has a help text: `novel --help` describes `play`, `resume`, `read`,
  `routine` and `diagnose` instead of "run the script", and the `routine` subcommands, `read --port` and the options of
  `init`, `ingest`, `canon`, `character`, `campaign` and `example` say what they do.
- `novel play --help` is rewritten in plain English: "Consult", "registry", "Structured return for use in a chat"
  and the other word-for-word texts are gone, and the delays say they are in seconds.
- The possessions rules name `novel play --inventory --json` instead of the internal `play_narrative.py` script.
- The archivist rules no longer describe the search scripts of the original project, which "update files": they
  ask to use the save read-only and filter by date and recipient.
- The possessions rules said "buying the balance" for the French "acheter le complément": they now say buying what is
  still missing within the available funds.
- The character sheets page no longer mentions the original project, its private saves, a hunger rule or saves
  older than the first release.
- The common rules said that a missing knowledge attribution does not prove an NPC "ignores" a fact, a calque of the
  French "ignorer": they now say it does not mean the NPC does not know it.
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
