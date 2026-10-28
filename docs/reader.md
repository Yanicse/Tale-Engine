# Reader page

The abilities tab shows separate hit point and mana bars, mana costs, charges and cooldown availability.

`novel read` serves one local page on `127.0.0.1` (port 8765 by default, `--port` to change). It binds only to your
machine, loads no external resource (fonts and icons are bundled) and shows only what the player character may know.

## What it shows

Tabs: Story, Scene, NPC, Possessions, Agenda, Summaries, Abilities, and Creations when the campaign uses them. The input
box sends an action through the same path as `novel play`; the engine selector lists `codex`, `claude`, `api` and
`manual`. A pending action shows Resume, Retry and Re-evaluate buttons. Settings (Aa) change the text size, the
immersive mode and the ambient sound; dictation uses the browser's speech recognition where available.

## Presentation comes from the novel pack

The page holds no text or name from any universe. `/api/config` serves, from the campaign:

- `title`, `subtitle`, `input_hint`, `clock`, the character's name;
- `preset` and `colors`: `auto` follows the fictional hour (day, night) and the others fix a mood; colors override
  the preset's palette;
- `labels`: every text of the page in the novel's language, with the pack's own overrides ([Prompts and languages](prompts-and-languages.md));
- `icons`: your word patterns for objects, abilities and places, tried before the built-in ones;
- `engines` and `default_engine`.

Existing campaigns keep the presentation they were created with.

## Icons

Icons come from [game-icons.net](https://game-icons.net) (CC BY 3.0, credits in `novel_engine/resources/CREDITS.md`
with the licenses of the bundled fonts). An item gets the first icon whose pattern matches a word of its name or
description; patterns of your `theme.json` come first. Valid names are the files of `novel_engine/resources/icons`.

## Endpoints

All `GET`, JSON, player audience only: `/api/state`, `/api/config`, `/api/story?n=&before=`, `/api/search?q=`,
`/api/possessions`, `/api/npc`, `/api/agenda`, `/api/summaries`, `/api/abilities?q=`, `/api/creations`, `/api/turn`.
`POST /api/turn` sends an action (`{text, engine, mode}`); only one turn can run at a time.
