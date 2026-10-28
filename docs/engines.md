# Engines

An engine is what answers the narrator's request. Four are supported, for playing (`novel play`, the reading page)
and for building the canon (`novel canon build`). The same engine serves the narrator and the periodic planner.

| Engine | Needs | How it runs |
| --- | --- | --- |
| `claude` | Claude Code CLI on the PATH (or `--claude PATH`) | one `claude -p` call per turn with no tools, no MCP server and no kept session; the answer is checked against the schema |
| `codex` | Codex CLI on the PATH (or `--codex PATH`) | one `codex exec` call per turn in a read-only sandbox with tools, web search and plugins disabled, Standard service tier |
| `api` | `ANTHROPIC_API_KEY` in the environment | one call to the Anthropic Messages API that forces a single tool carrying the answer schema; standard library only |
| `manual` | nothing | the request is written to a file, you answer it with any model, the engine reads the answer |

`novel doctor` shows which ones can run. When you do not choose, `novel play` uses the choice saved for the campaign
(`campaigns/<slug>/novel_engine.json`, written when you pick one in a terminal) if it still works, else the first
available of `claude`, `codex`, `api`, `manual`. The reading page lists all four and disables the unavailable ones.

Default models: `claude-sonnet-5-5` for `claude` and `api`, `gpt-6.1-sol` for `codex`; change it with `--model`.
`--effort` and `--delay` (default 240 seconds) set the reasoning effort and the time allowed per generation; the
planner has its own `--planner-effort` and `--planner-delay`.

## What every engine goes through

Whatever transport is used, the answer goes through the same path: a receipt is saved before validation, the answer
is validated against the turn schema and the engine's rules, and only then are the events applied. A failed or invalid
answer keeps the player's action pending; nothing is lost (see [Architecture](architecture.md)).

## Manual engine, step by step

```bash
novel play --text "I take the oars." --engine manual
```

1. The engine writes two files in `campaigns/<slug>/manual/`: `turn.prompt.md` (instructions, the turn and the JSON
   Schema of the answer) and `turn.state.json` (a fingerprint of the request). It stops with a message that names them.
2. Send `turn.prompt.md` to any model and ask for one JSON object only. Save it as `turn.response.json`.
3. Run `novel play --resume --retry --engine manual`. The answer is used only if it answers that exact request.

The periodic planner uses `review.prompt.md` and `review.response.json` the same way. If you change the action or
the state moves on, a new request replaces the files and the old answer is discarded. An invalid answer is reported;
fix the file and run the command again.

## API engine

The key is read from `ANTHROPIC_API_KEY` for each call. It is never written to a file, a receipt or the saved metrics.
Errors (HTTP status, network, an answer cut at the output limit, an answer without the structured result) keep the
action pending like any other failure.
