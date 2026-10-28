# Command reference

Run `novel --help`, or `novel <command> --help`, for the live text.

| Command | What it does |
| --- | --- |
| `novel doctor` | checks Python, SQLite FTS5, Claude CLI, Codex CLI, API key and pypdf; exit code 1 if a requirement fails |
| `novel init <slug> [--title T] [--language L] [--preset P]` | creates `novels/<slug>/` with `novel.json` and `theme.json` |
| `novel ingest <file> --novel <slug> [--volume N]` | extracts the text of a `.txt`, `.md`, `.epub` or `.pdf` book into `text/vNN.jsonl` |
| `novel canon plan <slug> [--budget N]` | prints the batches and the estimated tokens still to send; sends nothing |
| `novel canon build <slug> [--engine E] [--model M] [--budget N] [--limit N] [--yes]` | builds or resumes the canon; `--engine` is `manual` (default), `claude`, `codex` or `api`; remote engines need `--yes` |
| `novel canon search <slug> <words>` | looks up the built canon, locally: each claim with the volume, page and quote that source it; no model, no account |
| `novel character new <slug> [--name N] [--hp N] [--mana N] [--description T] [--ability ID:LABEL[:COST]] [--force]` | writes `character.json`; asks on a terminal for what you leave out |
| `novel validate [<slug>]` | lists every error and warning of a pack (all packs without a name); exit code 1 on errors |
| `novel campaign new <slug> [--start PLACE] [--no-creations] [--bounded-recovery]` | creates `campaigns/<slug>/campaign.sqlite` from a validated pack and selects it. The campaign keeps the creations register, selects the canon claims that match each turn for the narrator, installs the periodic planner (summaries) and, when the pack ships them, the chronicler and the arcs; `--no-creations` and `--bounded-recovery` change the two optional formats (see `novel configure`) |
| `novel example [--slug S] [--world]` | installs the bundled example novel and starts its campaign; `--world` adds the example chronicler (two clocks) and arcs, which make the planner review the world on the first turn |
| `novel play ...` | plays turns; every option below is passed to it |
| `novel resume` | shows the active campaign and its pending action, without changing anything |
| `novel read [--port P] [--no-browser]` | serves the reading page on 127.0.0.1 (port 8765 by default) |
| `novel unblock [--campaign P] [--apply]` | previews, then with `--apply` grants one more generation (backed up first) to a pending action whose budget of two generations is spent |
| `novel configure [--creations on\|off] [--bounded-recovery on\|off] [--canon-selection on\|off] [--campaign P] [--apply]` | previews, then switches the creations register, the bounded automatic recovery or the canon selection of an existing campaign (backed up first); `--canon-selection on` gives a campaign created before it was the default the sourced claims that match each turn |
| `novel diagnose <campaign.sqlite> [--request ID]` | read-only view of how the context of one attempt was selected (what memory was kept or left out, and why); never changes the save |
| `novel routine ...` | handles periods of several days delegated to the narrator, see [Routines](routines.md) |

## `novel play`

| Option | Meaning |
| --- | --- |
| `--text T` / `--text-file F` | plays one action and exits (without it, an interactive loop starts) |
| `--engine codex\|claude\|api\|manual` | the engine; by default the saved choice, else the first engine available |
| `--model M`, `--effort`, `--delay`, `--words` | model name, reasoning effort, seconds allowed per generation, target length |
| `--resume` | processes the pending action again |
| `--retry` | allows a new model call after a failure or an interruption (needed with `--resume`) |
| `--reevaluate` | re-evaluates an action that was rejected |
| `--result ID` | shows a recorded result again, with no model call |
| `--agenda`, `--summaries`, `--inventory`, `--skills`, `--memories Q`, `--files`, `--creations Q` | local consultations: no turn, no model |
| `--json` | structured output; `--measures` prints durations and usage on stderr |

Statuses in the JSON output: `recorded` and `question` (the narration is saved and shown), `resume`, `memory_required`,
`technical_stop` (stopped, nothing lost), and a refusal with a reason.

## Environment

| Variable | Use |
| --- | --- |
| `NOVEL_HOME` | folder that holds `novels/`, `campaigns/` and `corpus/` |
| `ANTHROPIC_API_KEY` | enables the `api` engine; read from the environment only, never stored |
