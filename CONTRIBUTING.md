# Contributing

Thanks for helping. A few rules keep the project easy to trust and to install.

## Set up

```bash
git clone <your fork>
cd novel-engine
pip install -e .
python -m unittest discover -s tests
```

The suite takes a couple of minutes and needs no account, no network and no model: engines are replaced by local fakes
(`tests/fake_claude.py`, `tests/fake_codex.py`, a local HTTP server for the API engine).

## Rules

- **Standard library only.** The package has no required dependency; `pypdf` is the single optional one. Do not add
  another without a very good reason.
- **Python 3.10 and up**, on Linux, macOS and Windows. Code that depends on the platform lives in `novel_engine/proc.py`.
- **A change comes with a test.** Fix a bug with a test that failed before. The docs have tests too (`tests/test_docs.py`):
  when you change a command, a default or a page text, the documentation must say the same.
- **The program keeps the truth.** A model proposes; validation and rules decide. Do not let a prompt change replace a check.
- **Prompts are files.** Edit `novel_engine/prompts/en/*.md`, not strings in code. Keep the `{placeholders}` the code fills.
- **Page texts are keys.** Add a key to `novel_engine/ui_text.py` in English and in every language pack
  (`tests/test_ui_text.py` checks it), then use it in the page with `tr('key')` or `data-t`.
- **No books, no canon, no saves in git.** Never commit `*.sqlite`, book files, `text/` or `work/` of a pack. The
  bundled example is an original story: keep example data original too.
- **Use `novel validate` and the example** (`novel example --slug scratch`) to try a change end to end.

## Checks that run for you

`tests/test_no_private_traces.py` fails on personal home paths, real e-mail addresses, databases and book files in the
repository. It can also check names from a private list: put one name per line in a file called `.private_terms.txt` at
the repository root (it is ignored by git) or point `PRIVATE_TERMS_FILE` at one. The CI checks the same terms through
hashes in `tests/private_term_hashes.txt`, so the names themselves are never published.

## Pull requests

Describe what changed and why, how you tested it, and anything the docs now say differently. Keep a pull request to one
subject. Update [CHANGELOG.md](CHANGELOG.md) under "Unreleased".

## Reporting a problem

Use the issue templates. Include the output of `novel doctor`, the command you ran and the message you got. Never paste
text from a book you do not have the right to share.
