# Getting started

## Install

Python 3.10 or newer is the only requirement. There is no mandatory dependency.

```bash
pip install .            # from a clone of the repository
pip install ".[pdf]"     # also reads PDF books (adds pypdf)
```

Then check the setup:

```bash
novel doctor
```

`doctor` checks Python, SQLite FTS5 (needed for search) and which engines can run here. Claude CLI, Codex CLI, an
`ANTHROPIC_API_KEY` and `pypdf` are optional. The **manual** engine needs nothing.

## Where your files go

`novels/` and `campaigns/` are created in the **current folder**. Set `NOVEL_HOME` to keep them somewhere
else. When you run from a clone of the repository, the clone itself is used.

## Try the example (no account, no model)

```bash
novel example          # installs "The Salt Lantern" and starts its campaign
novel read             # opens the reading page in your browser
```

The example is a short invented story. `novel example` copies its pack to `novels/lantern-bay`, builds its canon
from prepared answers (every quote is checked against the text), and creates the campaign with an opening scene.

To play a turn, type the action in the page and press Ctrl+Enter, or from the terminal:

```bash
novel play --text "I take the oars." --engine manual
```

With the manual engine the engine writes `campaigns/lantern-bay/manual/turn.prompt.md`. Send that file to any
chat model, save its JSON answer as `turn.response.json` in the same folder, then:

```bash
novel play --resume --retry --engine manual
```

With Claude CLI, Codex CLI or an API key, drop `--engine manual` and the turn runs by itself. See
[Engines](engines.md).

## Bring your own novel

```bash
novel init my-novel --title "My Novel" --language en
novel ingest book.epub --novel my-novel          # .txt, .md, .epub, .pdf
novel canon plan my-novel                        # shows the cost, sends nothing
novel canon build my-novel --engine claude --yes # or codex, api, manual
novel character new my-novel --name Ana --hp 40
novel validate my-novel                          # lists every problem of the pack
novel campaign new my-novel
novel read
```

Edit `novels/my-novel/novel.json` and `theme.json` between the steps: see [Novel packs](novel-packs.md). The canon
step is resumable and can be repeated safely: see [Canon](canon.md).
