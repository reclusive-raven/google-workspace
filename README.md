# google-workspace

A [Claude Code](https://claude.com/claude-code) skill that reads and writes native Google Docs and Google Sheets in place, through the Docs, Sheets and Drive APIs.

In a synced Google Drive folder, `.gdoc` and `.gsheet` files are only JSON pointer stubs, so an agent can see a document exists but not what is in it. This skill resolves a stub path, a `docs.google.com` / `drive.google.com` URL or a raw file ID to the document, then lets Claude:

- read a Doc as Markdown (whole, or one section by heading)
- append, insert, or find-and-replace text in a Doc
- read a Sheet's tabs, a whole tab, or specific ranges
- update, append, clear, or batch-update cells
- export a Doc or Sheet (PDF, DOCX, XLSX, CSV, Markdown and more)

It handles Shared drives, shortcuts and link-shared files with resource keys. Google Slides and Forms are out of scope.

## Install

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+.

```sh
git clone https://github.com/reclusive-raven/google-workspace ~/.claude/skills/google-workspace
```

Then do the one-time OAuth setup in [references/auth.md](references/auth.md): enable the three APIs in your own Google Cloud project, save a Desktop OAuth client as `secrets/credentials.json`, and run:

```sh
uv run --script ~/.claude/skills/google-workspace/scripts/gws.py auth login
```

Claude Code picks the skill up in new sessions and uses it whenever a Google Doc, Sheet or Drive link comes up. [SKILL.md](SKILL.md) is what Claude reads, and it lists every command.

## Write safety

- Reads are always safe. Destructive writes (replace, update, clear, batch-update) print a preview and do nothing until rerun with `--yes`; `--dry-run` previews any write.
- Files under folders named like `finances`, `medical` or `vault` count as sensitive: every write there needs `--yes` and takes a backup first. Tune the list in `scripts/gws_safety.py`.
- Backed-up writes can be undone with `gws.py restore`, and every write is logged to `secrets/writes.log`.
- The default token scopes can edit referenced Docs and Sheets but cannot create, move, trash or share files.
- `secrets/` (OAuth client, tokens, backups, log) is git-ignored.

## Tests

```sh
uv run --extra dev pytest                       # offline suite
uv run --extra dev python tests/live_smoke.py   # optional, against your real account; creates scratch files
```

## License

[MIT](LICENSE)
