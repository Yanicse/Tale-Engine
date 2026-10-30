# Security policy

## Supported versions

Only the latest release receives fixes.

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's private reporting instead:
**Security tab > Report a vulnerability** on this repository. You will get an answer as soon as possible.

## What to keep in mind

- Tale Engine runs locally. Your saves are SQLite files on your machine.
- A remote engine (Anthropic API, Claude CLI, Codex CLI) receives only what a turn needs, and only when you choose it.
  The API key is read from `ANTHROPIC_API_KEY` and never stored.
- A novel pack and its canon contain material from the book. Never commit or share them (the `novels/` folder is ignored by git).
