---
name: google-workspace
description: Reads and writes Google Docs and Google Sheets in place through the Google Docs, Sheets, and Drive APIs. Use whenever the user references a Google Doc, Google Sheet, Google Drive document, a .gdoc or .gsheet file, or a docs.google.com or drive.google.com link, and wants to read a doc as Markdown, read a sheet tab or range, append or insert text, find-and-replace, or update, append, or clear cells. Resolves stub files, URLs, and IDs and handles Shared drives, shortcuts, and resource keys. Do NOT use for local .docx, .xlsx, .csv, or .pdf files (use the docx, xlsx, and pdf skills) or for Google Slides or Forms.
---

# Google Workspace (Docs & Sheets, read + write)

Read and write native Google Docs and Sheets that are otherwise opaque on disk: the `.gdoc` /
`.gsheet` files in synced Drive folders are just JSON pointer stubs. This skill resolves a stub
(or a URL / raw id) to its document id and calls the Google APIs through one `uv`-run script.

## Golden rules

- **Operate only on an explicitly named target** — a stub path, a URL, or an id the user gave you.
  Never bulk-scan Drive for documents.
- **Account:** the tool acts as the account signed in with `auth login`. Pass
  `--account you@example.com` when a stub or URL might belong to a different identity; the
  tool blocks a write whose stub owner differs from the authenticated account (exit 3) and rejects a
  mismatched `--account` (exit 2).
- **Sensitive folders** (`finances/`, `medical/`, `work/`, `vault/`, `people/`, `academics/`,
  `_todo/`, `archive/`): before writing to anything under these, **confirm with the user first**. The
  tool forces a backup and requires `--yes` there — and now detects sensitivity for **URL/id targets
  too** (via the file's Drive folder), not just local stub paths.
- **Reads are safe; writes are gated.** Destructive writes (replace / update / clear / batch-update)
  refuse to run without `--yes` and print a preview instead. Prefer `--dry-run` when unsure. Every
  successful write is logged to `secrets/writes.log` and (when backed up) is reversible via `restore`.

## One-time setup

If `auth status` reports `email: None` or a scopes error, setup isn't done — see
[references/auth.md](references/auth.md). One-time: enable the Docs/Sheets/Drive APIs, put a Desktop
OAuth client at `secrets/credentials.json`, then run `auth login`.

## Entry point

    uv run --script ~/.claude/skills/google-workspace/scripts/gws.py <group> <cmd> [flags]

Add `--json` to any command for a machine-readable envelope (`{"ok", "op", ...}`); otherwise output
is human Markdown. Add `--resource-key KEY` for link-shared files (usually auto-detected from the
stub/URL). **Exit codes:** `0` ok · `2` re-auth needed · `3` destructive write needs `--yes`
(preview was printed, nothing written) · `4` not found · `5` no permission · `6` rate-limited ·
`7` doc changed since read.

## Workflow

Always **resolve → read/preview → (for writes) confirm → write**. You rarely need `resolve`
explicitly for reads (the `doc`/`sheet` commands resolve internally); use it to check a target's
type/name or that it's in a shared drive.

## Read a Doc

    gws.py doc read <target> [--format markdown|text|json] [--section "Heading"] [--max-chars N]

Returns Markdown (headings, lists, tables, links, emphasis). Use `--section` to pull just one
heading's content and `--max-chars` (default 40000) to bound output. See
[references/api.md](references/api.md).

## Write a Doc

    gws.py doc append  <target> --text "..." [--markdown]        # or --text-file PATH|-  (additive)
    gws.py doc insert  <target> --after-heading "H" --text "..."                          (additive)
    gws.py doc insert  <target> --after-text "foo" | --at-start | --index N --text "..."  (additive)
    gws.py doc replace <target> --find "a" --replace "b" [--no-match-case] --yes          (destructive)

`--markdown` writes formatted content (headings, bold/italic, bullets/numbers, links) instead of
plain text — so a read-as-Markdown → edit → write-back keeps its shape. Prefer the `--after-heading`
/ `--after-text` / `--at-start` anchors over a raw `--index` (indices are UTF-16 and easy to get
wrong). Writes use optimistic concurrency (revisionId); if the doc changed since read you get exit 7
— just re-run. `replace` without `--yes` previews the match count and stops. Details:
[references/writing.md](references/writing.md).

## Read a Sheet

    gws.py sheet read <target>                       # tabs + dimensions only (safe default)
    gws.py sheet read <target> --tab "Name"          # a whole tab
    gws.py sheet read <target> --range "Sheet1!A1:D50" [--render formula] [--format csv]
    gws.py sheet read <target> --ranges A1:B2 C1:D9   # several ranges in one call

No range → prints only tab names + sizes (prevents dumping a huge sheet). `--render formula` shows
formulas instead of values. Reads cap at `--max-rows`/`--max-cols` with a notice. See
[references/api.md](references/api.md).

## Write a Sheet

    gws.py sheet update <target> --range "S!B2" --values-json '[["done"]]' --yes          (destructive)
    gws.py sheet append <target> --range "Log!A1"  --values-json '[["2026-07-04","ok"]]'  (additive)
    gws.py sheet clear  <target> --range "Tmp!A1:Z99" --yes                               (destructive)
    gws.py sheet batch-update <target> --data-json '[{"range":"S!A1","values":[["x"]]}]' --yes  (destructive)

`--input user` (default) parses formulas/dates like the UI; `--input raw` stores literals (use for
IDs, codes, leading zeros). `append` never overwrites (INSERT_ROWS). `batch-update` writes several
ranges atomically (all or none). Values via `--values-json` (array of arrays) or `--values-csv PATH|-`.

## Export a Doc or Sheet

    gws.py doc   export <target> --as pdf|docx|txt|md|html [--out PATH]
    gws.py sheet export <target> --as xlsx|csv|tsv|pdf     [--out PATH]

Downloads a snapshot to disk (default filename from the doc's name). `csv`/`tsv` cover the first
sheet only.

## Write safety & undo

`--dry-run` previews any write and never mutates. Destructive ops need `--yes`. `--backup` (auto-on
for sensitive folders) snapshots the pre-write state to `secrets/backups/`. To undo, feed that
snapshot back:

    gws.py restore <secrets/backups/…json> --yes

`restore` reverts `sheet update` / `sheet clear` / `sheet batch-update` (writes the values back) and
`doc replace` (reverses it — clean when the replacement text was unique). Additive ops (append /
insert) have no automatic undo. Every mutation is also appended to `secrets/writes.log` (op, id,
account, backup).

## Errors & re-auth

On exit 2, run `gws.py auth login`. On 5 with "API not enabled", enable the API in the Cloud project.
Full matrix: [references/errors.md](references/errors.md).

## Not handled

`.gslides` / `.gform` and other Google types → this skill declines and suggests `files.export`
(Slides → the pptx skill). Local `.docx`/`.xlsx`/`.csv`/`.pdf` → use the docx / xlsx / pdf skills.
