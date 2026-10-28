# Prompts and languages

There are three separate languages in a campaign.

| What | Where it is set | Default |
| --- | --- | --- |
| the language of the **narration** | `language` in `novel.json` | `en` |
| the language of the **reading page** | the same `language`, through a language pack | English when no pack exists |
| the language of the narrator's **instructions** | `novel_engine/prompts/<language>/` | `en` |

## The reading page

Every text of the page is a key of `novel_engine/ui_text.py` (about 100 keys, with `{placeholders}` such as `{name}`).
English is the reference and `fr` is bundled. A language pack translates; a missing key falls back to English.

To change a few texts for one novel, set `labels` in `theme.json`:

```json
{"labels": {"tab.npc": "People", "tab.summaries": "Story so far", "send": "Go"}}
```

`novel validate` rejects unknown keys and warns when a label drops a placeholder. To add a language, add a dictionary to
`PACKS` in `ui_text.py` with the same keys (a test checks the keys and the placeholders) and a name in `LANGUAGE_NAMES`
if you want `French`-style values to resolve.

## The narrator's instructions

The long instruction texts are files, read exactly as written: `novel_engine/prompts/en/<module>.<name>.md`, for
example `instructions.opening.md` or `agenda_protocol.instructions.md` (30 files). Code fills a few placeholders with
`str.format` (`{word_target}` in `instructions.story.md`; `{limit}`, `{first}`, `{last}` and `{excerpt}` in
`canon_build.prompt.md`), so keep those and double any other brace you add.

To translate them, create `novel_engine/prompts/<language>/` and copy the files you translate under the same names.
`prompts.load(name, language)` falls back to English for the rest. Nothing selects another language yet: the engine
reads `en`. Wiring a second folder to a setting is the missing piece, and a test fails if a file is orphaned or a
prompt the code asks for is missing.

The narrator is told to write in the campaign's narration language whatever the language of its instructions.
