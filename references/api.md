# API reference — resolution, Docs, Sheets, rendering

All Drive calls set `supportsAllDrives=true`. When a file has a non-empty `resource_key` (link-shared),
the header `X-Goog-Drive-Resource-Keys: <id>/<key>` is added to every request for it (handled in
`gws_http.execute`). Resource keys come from the stub JSON, a URL `?resourcekey=`, or `--resource-key`.

## Resolution (gws_drive.py)

A **target** is a local `.gdoc`/`.gsheet` stub path, a `docs.google.com`/`drive.google.com` URL, or a
raw id.

- `parse_target()` is pure-local: it reads the stub JSON (`doc_id` + `resource_key`), or extracts the
  id/type/resourcekey from a URL, or accepts a bare id. Enough to address Docs/Sheets (they key off
  the id) with **no API call**.
- `resolve()` adds a Drive `files.get` for name/mimeType/driveId/resourceKey/**parents** and follows
  shortcuts (`shortcutDetails.targetId`). It also walks the parent folders
  (`ancestor_folder_names`) and returns a `sensitive` verdict + `folder_path`, so sensitivity is
  known for URL/id targets, not just local stubs. Writes resolve with this metadata; reads stay
  local. Used by the `resolve` command and every write.

**Account guard:** the authenticated email is cached at `auth login` (`secrets/account.json`).
`--account` asserts it (mismatch → exit 2); a write whose stub `email` differs blocks (exit 3) unless
`--yes`; a read only warns. `secrets/writes.log` records every mutation.

Stub JSON has four keys — `""` (a warning banner), `doc_id`, `resource_key` (often empty), `email` —
parse defensively. mimeType → type: `document`→doc, `spreadsheet`→sheet, `presentation`→slides,
`form`→form, `shortcut`→follow. Only **doc** and **sheet** are natively supported.

## Docs (gws_docs.py)

**Read:** `documents.get(documentId, includeTabsContent=true, fields="title,revisionId,tabs")`. With
`includeTabsContent` the legacy top-level `body`/`lists`/`inlineObjects` fields are **disallowed** in
the mask — each tab carries its own `documentTab.body`/`lists`/`inlineObjects`. Walk `body.content[]`
→ `paragraph`/`table`; paragraph `elements[]` → `textRun` (+ `textStyle`); heading from
`paragraphStyle.namedStyleType`; list bullet from `bullet` resolved against the tab's `lists`; tables
recurse over `tableRows[].tableCells[].content`. Multi-tab docs walk every `documentTab.body`.

**Write:** get `revisionId` immediately, then `documents.batchUpdate` with
`writeControl.requiredRevisionId` (concurrent edit → 400 → exit 7). Request shapes:

```jsonc
{"insertText": {"text": "…\n", "endOfSegmentLocation": {}}}          // append
{"insertText": {"text": "…", "location": {"index": 25}}}             // insert (UTF-16 index; body starts at 1)
{"replaceAllText": {"containsText": {"text": "old", "matchCase": true}, "replaceText": "new"}}
```

Notes: indices/lengths are **UTF-16 code units**; every paragraph ends in `\n`; the very last
newline can't be inserted past.

**Markdown writes** (`--markdown`, `gws_md2docs.py`): one `insertText` of the whole plain text at a
concrete index, then `updateParagraphStyle` (headings), `createParagraphBullets` (lists, no leading
tabs so indices don't shift), and `updateTextStyle` (bold/italic/link/code) over `startIndex+offset`
ranges. Anchors (`find_index`): `--at-start`, `--after-heading` (paragraph `endIndex`), `--after-text`
(textRun `startIndex` + UTF-16 offset).

**Export** (`gws_drive.export_file`): `files.export_media(fileId, mimeType)` + `MediaIoBaseDownload`
(pdf/docx/txt/md/html for docs; xlsx/csv/tsv/pdf for sheets — csv/tsv = first sheet only).

## Sheets (gws_sheets.py)

**Metadata first:** `spreadsheets.get(fields="properties.title,sheets(properties(sheetId,title,index,gridProperties(rowCount,columnCount)))")`
— `sheet read` with no range prints only this.

**Read values:** `values.get(range, valueRenderOption=FORMATTED_VALUE|UNFORMATTED_VALUE|FORMULA,
dateTimeRenderOption=FORMATTED_STRING, majorDimension=ROWS)`; multiple ranges →
`values.batchGet(ranges=[...])`.

**Write values:**

```python
values.update(range, valueInputOption, body={"values": [[...]]})                 # overwrite
values.append(range, valueInputOption, insertDataOption="INSERT_ROWS", body=...)  # never overwrites
values.clear(range, body={})                                                      # clear
values.batchUpdate(body={"valueInputOption":…, "data":[{range,values},…]})        # atomic multi-range
```

**Undo:** destructive writes snapshot the FORMULA-rendered before-image to `secrets/backups/`;
`restore` writes it back with **USER_ENTERED** (inverts the FORMULA render → formulas/dates return
intact). `doc replace` restore reverses the replace.

`valueInputOption`: **USER_ENTERED** (default) parses input like the UI — `=SUM(A1:A2)`→formula,
`7/4/2026`→date, may drop leading zeros; **RAW** stores the literal string (use for IDs/codes).
A1 ranges with spaces in the tab name must be quoted: `'My Tab'!A1:B2`. Sheets values have **no
revision token** → the guard for destructive writes is the `--backup` pre-image.

## Rendering (gws_render.py)

**Docs → Markdown:** headings `#`–`######`; `**bold**` / `*italic*` / `***both***` / `~~strike~~`;
super/sub via `<sup>`/`<sub>`; links `[text](url)`; unordered `-`, ordered `1.` with 2-space indent
per nesting level (item numbers are counted per `listId`+level — the API omits them; a heading resets
them); tables → GitHub pipe tables (escape `|`, newlines → `<br>`); inline images → `![alt](uri)`
(alt is durable; `contentUri` expires ~30 min). `--format text` runs the same walk without markers.
`--section "H"` slices from a matching heading to the next same-or-higher heading. `--max-chars`
truncates with a notice.

**Sheets → text:** `--format markdown` (first row as header) / `csv` / `tsv` (RFC-style quoting) /
`json` (array of arrays). Reads cap at `--max-rows`×`--max-cols` with a truncation notice; an empty
range renders `(empty range)`.
