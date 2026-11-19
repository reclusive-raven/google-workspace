# Writing formatted content & choosing insert positions

## `--markdown` (formatted doc writes)

`doc append --markdown` and `doc insert --markdown` interpret the text as a Markdown **subset** and
translate it to native Docs formatting in one atomic `batchUpdate`:

| Markdown | Becomes |
|---|---|
| `# … ###### ` | Heading 1–6 (named styles) |
| `**bold**`, `*italic*` / `_italic_`, `***both***` | bold / italic / bold-italic runs |
| `` `code` `` | monospace run |
| `[text](url)` | linked text |
| `- ` / `* ` / `+ ` item | bulleted list (disc) |
| `1. ` / `1) ` item | numbered list (consecutive items share one list → continuous numbering) |
| blank line | paragraph break |

**Not supported on the write side:** tables and images (a table/image line is written as plain text).
Reading still renders tables and images — this limit is only for writing. Nested list levels are
written flat (single level) in this version.

How it works: the whole plain text is inserted once at the target index, then styling requests
address absolute positions computed from that index. Offsets are UTF-16 code units (the unit Docs
uses), so emoji and other astral characters are counted correctly.

`append --markdown` adds a blank line before the new content so it starts cleanly after whatever was
already there.

## Insert anchors (`doc insert`)

Give exactly one of these to say **where** to insert — prefer an anchor over a raw index:

- `--at-start` — the very start of the body.
- `--after-heading "Heading text"` — just after the paragraph whose heading/title text matches
  (case-insensitive). Include a leading `\n` (or use `--markdown`) if you want the new text on its
  own line rather than merged into the following paragraph.
- `--after-text "some text"` — just after the first occurrence of that literal text (case-sensitive).
- `--index N` — a raw UTF-16 index (fragile; use only when you already know it, e.g. from
  `doc read --format json`).

Anchors resolve within one tab: the tab from the URL (`?tab=…`) if present, else the first tab. Use
`--tab-id t.xxxxxx` to target another tab.

## Restoring a write

Destructive writes drop a JSON snapshot in `secrets/backups/` (auto for sensitive folders, or with
`--backup`). `restore <snapshot> --yes` reverts it:

- `sheet update` / `sheet clear` / `sheet batch-update` → the captured cells are written back with
  `USER_ENTERED`, so formulas, numbers, and dates come back as they were.
- `doc replace` → the replace is reversed (`replace`→`find`); clean when the replacement string was
  unique in the document.
- `append` / `insert` are additive and have **no** automatic undo — remove the added content by hand.
