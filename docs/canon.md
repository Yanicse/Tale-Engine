# Canon

The canon is the book turned into sourced facts, stored in `novels/<slug>/canon.sqlite`. The narrator receives only
the few facts relevant to a turn, never the whole book.

## Steps

```bash
novel ingest book.epub --novel my-novel      # text -> text/v01.jsonl, numbered pages
novel canon plan my-novel                    # batches and estimated tokens, sends nothing
novel canon build my-novel --engine claude --yes
```

`ingest` accepts `.txt`, `.md`, `.epub` and, with `pip install ".[pdf]"`, `.pdf`. Run it once per volume; volumes are
numbered in order, or with `--volume N`.

## How the build works

1. Pages are grouped into batches under `--budget` input tokens (12000 by default); a batch never crosses a volume.
2. Each batch is sent to the engine with an extraction prompt. The answer is a list of entities and claims.
3. **Validation.** A claim is kept only if it has a known subject, a statement of at most 400 characters, and a
   `quote` of the excerpt that is found in a page of the batch (case, quote marks, dashes and spacing are ignored).
   Its page is the page where the quote was found. If no claim survives, the batch fails and is not stored.
4. Each batch is committed on its own. Then entities are merged (same name, or an alias that names another entity of
   the same kind), duplicate statements are dropped and the search index is rebuilt.
5. A volume is marked `closed` once every one of its pages has been extracted.

The result prints the claim count, the batches still `remaining`, those that `failed`, and the `volumes` status.

## Resuming and cost

A stopped run resumes where it stopped. Batches are planned over the pages not yet extracted, so you can change
`--budget` between runs without sending anything twice. `canon plan` and the line printed before a build show the
estimate for what is left; remote engines send nothing until you add `--yes`. `--limit N` stops after `N` batches.

## The manual engine

`--engine manual` (the default) needs no account. For each batch it writes `work/<batch>.prompt.md`. Send it to any
model, save the JSON answer as `work/<batch>.response.json`, run the command again. A batch whose answer is missing
is listed as `awaiting_manual_answer`; a batch whose answer is invalid is reported and can be fixed in place.

## What is stored

`entities`, `aliases`, `raw_claims` (with the quote), `consolidated_usable_claims`, `sources` (volume, page, quote),
`consolidated_fts` (search), `build_pages` and `build_batches` (progress). Existing databases are upgraded in place on
the next build.

## Legal note

Use books you own. The book files, `text/`, `work/` and `canon.sqlite` contain material from the book (the quotes
are verbatim passages) and are ignored by git. Do not commit, share or publish them.
