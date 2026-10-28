# Tale Engine documentation

Tale Engine turns a novel you own into a sourced, resumable interactive campaign. It reads the book, builds a
canon of sourced facts, and lets a model narrate your character's story while the engine keeps the state, the
secrets and the world's clock.

| Page | Read it to |
| --- | --- |
| [Getting started](getting-started.md) | install, check your setup, play the bundled example in five minutes |
| [Command reference](cli.md) | every `novel` command and option |
| [Novel packs](novel-packs.md) | the files that make one novel playable: `novel.json`, `character.json`, `theme.json` |
| [Character sheets](character-sheets.md) | passives, abilities, costs and forbidden resources |
| [Canon](canon.md) | ingest a book, build the canon, quotes, resuming, cost estimate, legal note |
| [Engines](engines.md) | Codex CLI, Claude CLI, Anthropic API, or manual copy and paste |
| [Reader page](reader.md) | the local reading page, themes, icons, languages |
| [Prompts and languages](prompts-and-languages.md) | translate the page or the narrator's instructions |
| [Architecture](architecture.md) | how a turn works, what is stored, where the safeguards are |
| [Troubleshooting](troubleshooting.md) | the errors you are most likely to meet |

Two things to keep in mind from the start:

- **Use books you own.** The book, its extracted text and the canon built from it stay on your machine. They are
  ignored by git and must never be committed or published.
- **The program keeps the truth, the model proposes.** A model never writes the save directly: its answer is
  validated against a schema and the engine's rules, then applied by the program in one transaction.
