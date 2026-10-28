# Troubleshooting

Start with `novel doctor` and `novel validate`.

| You see | Why and what to do |
| --- | --- |
| `[FAIL] SQLite FTS5` | your Python's SQLite lacks full-text search; install a standard Python build |
| `No novel pack "x"` | run `novel init x`, and check you are in the folder that holds `novels/` (or set `NOVEL_HOME`) |
| `No canon yet: run novel canon build first` | build the canon before `novel campaign new` |
| `Fix the errors above first` | `novel campaign new` found errors in the pack; `novel validate x` lists them |
| `Campaign already exists` | delete the campaign folder yourself if you want a new one; the tool never overwrites a save |
| `Claude CLI not found` / `Codex CLI not found` | install it, or give the path with `--claude` / `--codex`; or use `--engine manual` |
| `The Claude CLI found is a .cmd/.bat shim` | on Windows an npm-installed `claude.cmd` cannot take the answer contract on its command line (8191 characters). Give the native `claude.exe` with `--claude`, or use another engine |
| `ANTHROPIC_API_KEY is not set` | export it, or pick another engine |
| `PDF extraction needs the optional dependency` | `pip install "tale-engine[pdf]"` |
| `Generation interrupted or invalid; input kept` | the action is saved and pending. Fix the cause, then `novel play --resume --retry` |
| `A generation was already attempted. Use --resume --retry` | add `--retry` to allow a new call |
| `Generation budget reached for this action` | every action may use two generations (an outage or a refused answer counts); `--resume`, `--retry` and a new chat do not add one. `novel unblock` shows what it would do, `novel unblock --apply` grants exactly one more, with a backup |
| `Manual engine: send ... turn.prompt.md` | not an error: answer the request, save `turn.response.json`, run `novel play --resume --retry --engine manual` |
| the manual answer is ignored | it answers an older request: the files are rewritten when the request changes, so use the newest `turn.prompt.md` |
| `Response ... invalid; action kept` | the answer does not match the schema or a rule; the message says which field |
| canon batch `failed (no claim with a quote found in the excerpt)` | the model changed the quotes or answered another excerpt; run the build again |
| canon batch `awaiting_manual_answer` | save `work/<batch>.response.json`, run the build again |
| port already in use | `novel read --port 8800` |
| the page ignores my `theme.json` edit | the campaign keeps the presentation it was created with; create a new campaign |
| `Insufficient mana` | the turn was refused without spending resources; choose a cheaper ability or explicitly recover mana in the story |
| `Unsupported mechanics version` | the former health-based magic mode is not converted automatically; create a new campaign from an updated pack ([Character sheets](character-sheets.md)) |

Windows paths, the console encoding and the Claude desktop app's environment are handled by the tool; if a CLI engine
still fails, run `novel play --text "..." --engine claude --measures` and read the failure category on stderr.
