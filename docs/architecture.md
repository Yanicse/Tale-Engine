# Architecture

```
book -> text/vNN.jsonl -> canon.sqlite -> campaign.sqlite -> turn -> validation -> save -> reading page
        (ingest)          (canon build)   (campaign new)     (play)
```

Everything that matters is in SQLite. The engine is Python's standard library only.

## The two databases

- **`canon.sqlite`** (per novel, read-only during play): entities, aliases, claims with their quotes and pages, and a
  full-text index. A campaign points to it; a turn gets a bounded selection of private, sourced facts, never the book.
- **`campaign.sqlite`** (per campaign): the state (positions, resources, possessions, powers, relations, commitments), the
  event log, the exchanges shown to the player, requests and their recorded results, the world agenda, the summaries,
  the indexed memory and the technical budgets. Progress lives here, not in git and not in a chat.

## A turn

1. **Save the input.** The player's action is stored as a pending action, with a request identifier, before any call.
2. **Prepare the context.** The loop builds a packet: current state, recent exchanges, targeted memory, a selection of
   canon facts, the world agenda, summaries, abilities, possessions, the novel's world rules. Instruction blocks are added
   only for the topics the action touches. Each piece of information carries its audience.
3. **Ask the engine** ([Engines](engines.md)). The narrator returns one JSON envelope: narration, structured events
   (each with causes and an audience), blocking questions, memory requests and updates, agenda commands.
4. **Validate.** The answer is checked against the schema, the allowed references, and local rules: possessions and
   money and mana costs are computed by the program, consequences must match the final state, text coherence is checked, and a
   forbidden character such as U+2014 is replaced before saving.
5. **Apply and record** in one transaction, keyed by the request identifier, so a repeated request returns the same result.
6. **Display** only after the save.

Every failure keeps the action pending. `--resume --retry` calls the engine again, `--reevaluate` re-evaluates a
rejection, `--result ID` shows a recorded result with no call. A durable budget per action stops repeated failures.

## Who knows what

Events and facts name their recipients: `narrator`, `player`, `npc:<id>`. The reading page and the consultation commands
read the `player` audience only. Tracked NPCs and factions act through private agenda steps the player does not see.
Canon facts are private to the narrator; knowing a fact in the book does not mean the character knows it.
A testimony or an offer is not a verified fact, and a creation (an invented detail) is recorded as such.

## The world keeps moving

The agenda holds deadlines on the fictional clock; the planner periodically summarizes the story and reviews the world's
arcs, so events happen elsewhere while the player acts. The player's message always interrupts background work.

## Where the code is

| Area | Modules |
| --- | --- |
| turn loop | `narrative`, `narrative_preparation`, `narration_*`, `engines`, `validation`, `contracts` |
| state and save | `campaign`, `schema`, `recovery`, `bounded_resume`, `resume`, `attempt_archives` |
| memory and canon | `memory`, `indexed_memory`, `canon_selection`, `targeted_context`, `canon_build`, `ingest` |
| world | `agenda`, `agenda_protocol`, `planner`, `arcs`, `routine`, `background`, `scene_pacing` |
| rules | `possessions`, `character`, `abilities`, `magic`, `cooldowns`, `consequences`, `creations` |
| packs and page | `pack`, `pack_check`, `ui_text`, `reader`, `reader_page.html`, `scripts/read_game.py` |
| texts | `prompts`, `prompts/en/*.md`, `assets/docs/engine/` (agent rules, contracts, examples) |

`assets/docs/engine/` is what the narrator reads (common rules, per-role instructions, JSON Schemas under
`contracts/`), not documentation for you. This folder is.
