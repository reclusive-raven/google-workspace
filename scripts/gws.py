#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "google-api-python-client>=2.130",
#   "google-auth>=2.30",
#   "google-auth-oauthlib>=1.2",
# ]
# ///
"""google-workspace skill entry point.

    uv run --script gws.py <group> <cmd> [flags]

Groups: auth (login|status), resolve, doc (read|append|insert|replace), sheet (read|update|append|clear).
Add --json for a machine-readable envelope. Exit codes are the caller contract (see gws_http).
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys

import gws_docs
import gws_sheets
import gws_safety
from gws_auth import (
    AuthError, account_email, auth_status, cached_account_email, resolve_credentials,
)
from gws_drive import SUPPORTED, export_file, parse_target, resolve
from gws_http import (
    EXIT_AUTH, EXIT_GENERIC, EXIT_NEEDS_CONFIRM, EXIT_OK, GwsError,
)

DESTRUCTIVE = "destructive"
ADDITIVE = "additive"

_DOC_EXPORT = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain",
    "md": "text/markdown",
    "html": "text/html",
}
_SHEET_EXPORT = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",              # first sheet only (Drive limitation)
    "tsv": "text/tab-separated-values",
    "pdf": "application/pdf",
}


# --------------------------------------------------------------------------- output helpers
def _out(args, *, op, content=None, data=None):
    if args.json:
        env = {"ok": True, "op": op}
        if data:
            env.update(data)
        if content is not None:
            env["content"] = content
        print(json.dumps(env, ensure_ascii=False))
    elif content is not None:
        print(content)
    elif data:
        for k, v in data.items():
            print(f"{k}: {v}")
    return EXIT_OK


def _emit_preview(args, op, preview, exit_code, note=""):
    if args.json:
        print(json.dumps({
            "ok": exit_code == EXIT_OK, "op": op,
            "dry_run": exit_code == EXIT_OK, "needs_confirm": exit_code == EXIT_NEEDS_CONFIRM,
            "preview": preview, "note": note,
        }, ensure_ascii=False))
    else:
        print(preview)
        if note:
            print(f"\n{note}", file=sys.stderr)
    return exit_code


def _emit_error(args, err):
    code = getattr(err, "exit_code", EXIT_GENERIC)
    msg = getattr(err, "message", str(err))
    remedy = getattr(err, "remedy", "")
    if args.json:
        print(json.dumps({"ok": False, "error": {
            "code": getattr(err, "code", None), "message": msg, "remedy": remedy, "exit": code}},
            ensure_ascii=False))
    else:
        print(f"error: {msg}", file=sys.stderr)
        if remedy:
            print(f"-> {remedy}", file=sys.stderr)
    return code


# --------------------------------------------------------------------------- input helpers
def _load_text(args):
    if getattr(args, "text", None) is not None:
        return args.text
    tf = getattr(args, "text_file", None)
    if tf:
        return sys.stdin.read() if tf == "-" else open(tf, encoding="utf-8").read()
    raise GwsError("no text provided", remedy="Pass --text or --text-file (use - for stdin).")


def _parse_values(args):
    if getattr(args, "values_json", None):
        v = json.loads(args.values_json)
        if not isinstance(v, list) or not all(isinstance(r, list) for r in v):
            raise GwsError("--values-json must be a JSON array of arrays",
                           remedy='e.g. [["a","b"],["c","d"]]')
        return v
    vc = getattr(args, "values_csv", None)
    if vc:
        raw = sys.stdin.read() if vc == "-" else open(vc, encoding="utf-8").read()
        return [row for row in csv.reader(io.StringIO(raw))]
    raise GwsError("no values provided", remedy="Pass --values-json or --values-csv (use - for stdin).")


def _resolved(args, *, need_drive_meta=False, is_write=False):
    """Resolve the target to (creds, id, resource_key, local_path, gtype, info). Cheap by default:
    only calls the Drive API when need_drive_meta (writes, for sensitivity) or the type is ambiguous.

    Also asserts the authenticated identity against --account / the stub's email before returning,
    so a mismatched account can never slip through to a read or (especially) a write."""
    creds = resolve_credentials("write")
    rk = getattr(args, "resource_key", None)
    parsed = parse_target(args.target)          # pure-local; also yields the stub's owner email
    stub_email = parsed.get("stub_email")
    if need_drive_meta:
        info = resolve(creds, args.target, resource_key_override=rk)
        result = (creds, info["id"], info.get("resourceKey", ""), info.get("local_path"),
                  info.get("type"), info)
    else:
        result = (creds, parsed["id"], (rk or parsed["resource_key"]),
                  parsed.get("local_path"), parsed.get("hint_type"), None)
    _assert_account(args, stub_email, creds, is_write=is_write)
    return result


def _assert_account(args, stub_email, creds, *, is_write):
    """Enforce the account contract. Best-effort: only asserts when the authenticated email is known.
    - --account mismatch          -> exit 2 (wrong identity; re-auth or drop the flag)
    - stub owner != you, on WRITE -> exit 3 unless --yes (block the wrong-account write)
    - stub owner != you, on READ  -> warn on stderr only
    """
    want = getattr(args, "account", None)
    if not (want or stub_email):
        return
    have = account_email(creds)
    if not have:
        return  # can't verify identity (offline / no cache) - don't manufacture a false failure
    hv = have.strip().lower()
    if want and want.strip().lower() != hv:
        raise GwsError(f"authenticated as {have}, but --account requested {want}",
                       exit_code=EXIT_AUTH,
                       remedy=f"Re-run `gws.py auth login` as {want}, or drop --account.")
    if stub_email and stub_email.strip().lower() != hv:
        if is_write and not getattr(args, "yes", False):
            raise GwsError(
                f"this file's stub says it belongs to {stub_email}, but you're authenticated as {have}",
                exit_code=EXIT_NEEDS_CONFIRM,
                remedy=f"If writing as {have} is intended, re-run with --yes; otherwise auth as {stub_email}.")
        if not is_write:
            print(f"warning: stub belongs to {stub_email}; authenticated as {have}", file=sys.stderr)


def _gate(args, *, kind, local_path, info=None):
    """Return (need_confirm, do_backup, sensitive). Sensitive targets always confirm + backup,
    even for additive ops, so nothing under a sensitive folder is mutated silently.

    Sensitivity is the OR of the local stub path and the Drive-resolved folder (info["sensitive"]),
    so a target reached by URL/id is gated the same as one reached by its .gdoc/.gsheet stub."""
    sensitive = gws_safety.is_sensitive(local_path) or bool((info or {}).get("sensitive"))
    need_confirm = (kind == DESTRUCTIVE) or sensitive
    do_backup = bool(getattr(args, "backup", False)) or sensitive
    return need_confirm, do_backup, sensitive


def _journal(op, file_id, detail, backup_path):
    """Append one audit line for a successful mutation (best-effort)."""
    gws_safety.journal({"op": op, "id": file_id, "account": cached_account_email(),
                        "detail": detail, "backup": backup_path, "ok": True})


def _short(obj, cap=1500):
    """Compact a value for a preview so a wide range or long doc doesn't flood the terminal."""
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return s if len(s) <= cap else s[:cap] + f"… [+{len(s) - cap} more chars]"


def _default_out(name, ext):
    """A tidy default output filename from a document name."""
    base = re.sub(r"[^\w.-]+", "_", (name or "export")).strip("_") or "export"
    return f"{base}.{ext}"


def _confirm_note(sensitive, *, destructive):
    """The stderr note shown when a write stops for confirmation (exit 3)."""
    if destructive and sensitive:
        return "Destructive AND under a sensitive folder: re-run with --yes to apply."
    if destructive:
        return "Destructive: re-run with --yes to apply."
    return "Target is under a sensitive folder; re-run with --yes to proceed."


# --------------------------------------------------------------------------- auth
def cmd_auth_login(args):
    scope_mode = getattr(args, "scopes", "write")
    if args.force:
        from gws_auth import TOKEN_FILE, BACKUP_FILE
        for f in (TOKEN_FILE, BACKUP_FILE):
            try:
                f.unlink()
            except FileNotFoundError:
                pass
    resolve_credentials(scope_mode, allow_interactive=True)
    status = auth_status(scope_mode)
    return _out(args, op="auth.login", data={"authenticated_as": status.get("email"),
                                             "scopes": status.get("scopes"), "source": status.get("source")})


def cmd_auth_status(args):
    status = auth_status(getattr(args, "scopes", "write"))
    if args.json:
        return _out(args, op="auth.status", data=status)
    lines = [f"source:  {status.get('source')}",
             f"email:   {status.get('email')}",
             f"valid:   {status.get('valid')}",
             f"expiry:  {status.get('expiry')}",
             "scopes:"]
    lines += [f"  - {s}" for s in status.get("scopes", [])]
    return _out(args, op="auth.status", content="\n".join(lines))


# --------------------------------------------------------------------------- resolve
def cmd_resolve(args):
    creds = resolve_credentials("write")
    info = resolve(creds, args.target, resource_key_override=getattr(args, "resource_key", None))
    _assert_account(args, info.get("stub_email"), creds, is_write=False)
    return _out(args, op="resolve", data={k: info.get(k) for k in
                ("id", "type", "name", "driveId", "resourceKey", "isShortcut", "mimeType",
                 "local_path", "sensitive", "folder_path", "sensitivity_unverified")})


# --------------------------------------------------------------------------- docs
def _require_type(gtype, want, label):
    if gtype and gtype not in (want, None) and gtype in ({"doc", "sheet"} - {want}):
        raise GwsError(f"target is a {gtype}, not a {want}",
                       remedy=f"Use the {gtype} commands, or pass a {label}.")
    if gtype and gtype not in SUPPORTED and gtype is not None:
        hint = {"slides": "Google Slides (try the pptx skill via files.export)",
                "form": "Google Forms (no content API here)"}.get(gtype, gtype)
        raise GwsError(f"unsupported type: {gtype}",
                       remedy=f"This skill handles Docs & Sheets only; {hint}.")


def cmd_doc_read(args):
    creds, doc_id, rk, _, gtype, _ = _resolved(args)
    _require_type(gtype, "doc", "a .gdoc / document URL")
    tab_id = getattr(args, "tab_id", None) or parse_target(args.target).get("tab_id")
    content, meta = gws_docs.read(
        creds, doc_id, resource_key=rk, fmt=args.format, section=args.section,
        max_chars=args.max_chars, include_title=args.include_title, tab_id=tab_id)
    return _out(args, op="doc.read", content=content,
                data={"id": doc_id, "type": "doc", "revisionId": meta.get("revisionId"),
                      "title": meta.get("title"), "truncated": meta.get("truncated"),
                      "format": args.format, "tab_id": tab_id})


def cmd_doc_append(args):
    creds, doc_id, rk, local_path, gtype, info = _resolved(args, need_drive_meta=True, is_write=True)
    _require_type(gtype, "doc", "a .gdoc / document URL")
    text = _load_text(args)
    markdown = getattr(args, "markdown", False)
    need_confirm, do_backup, sensitive = _gate(args, kind=ADDITIVE, local_path=local_path, info=info)
    kind_label = "append-markdown" if markdown else "append"
    preview = f"[{kind_label}] would add {len(text)} chars to end of doc {doc_id}:\n---\n{text}\n---"
    if args.dry_run:
        return _emit_preview(args, "doc.append", preview, EXIT_OK)
    if need_confirm and not args.yes:
        return _emit_preview(args, "doc.append", preview, EXIT_NEEDS_CONFIRM,
                             note=_confirm_note(sensitive, destructive=False))
    backup_path = None
    if do_backup:
        backup_path = gws_safety.write_backup(doc_id, "doc-append", {
            "revisionId": gws_docs.current_revision(creds, doc_id, rk),
            "appended_text": text, "markdown": markdown})
    if markdown:
        result = gws_docs.append_markdown(creds, doc_id, text, resource_key=rk)
    else:
        result = gws_docs.append_text(creds, doc_id, text, resource_key=rk)
    _journal("doc.append", doc_id, {"chars": len(text), "markdown": markdown}, backup_path)
    return _out(args, op="doc.append", data={"id": doc_id, "appended_chars": len(text),
                "revisionId": (result.get("writeControl") or {}).get("requiredRevisionId"),
                "backup": backup_path})


def _resolve_insert_index(args, creds, doc_id, rk):
    """Turn the chosen anchor (--index / --after-heading / --after-text / --at-start) into a
    concrete UTF-16 index. Exactly one anchor must be given."""
    tab_id = getattr(args, "tab_id", None) or parse_target(args.target).get("tab_id")
    chosen = [(k, v) for k, v in (
        ("index", getattr(args, "index", None)),
        ("after_heading", getattr(args, "after_heading", None)),
        ("after_text", getattr(args, "after_text", None)),
        ("at_start", getattr(args, "at_start", False)),
    ) if v not in (None, False)]
    if len(chosen) != 1:
        raise GwsError("insert needs exactly one of --index / --after-heading / --after-text / --at-start",
                       remedy="Pick a single anchor for where to insert.")
    key, val = chosen[0]
    if key == "index":
        return int(val)
    return gws_docs.find_index(
        creds, doc_id, resource_key=rk, tab_id=tab_id,
        after_heading=val if key == "after_heading" else None,
        after_text=val if key == "after_text" else None,
        at_start=(key == "at_start"))


def cmd_doc_insert(args):
    creds, doc_id, rk, local_path, gtype, info = _resolved(args, need_drive_meta=True, is_write=True)
    _require_type(gtype, "doc", "a .gdoc / document URL")
    text = _load_text(args)
    markdown = getattr(args, "markdown", False)
    index = _resolve_insert_index(args, creds, doc_id, rk)
    need_confirm, do_backup, sensitive = _gate(args, kind=ADDITIVE, local_path=local_path, info=info)
    label = "insert-markdown" if markdown else "insert"
    preview = f"[{label}] would insert {len(text)} chars at index {index} of doc {doc_id}:\n---\n{text}\n---"
    if args.dry_run:
        return _emit_preview(args, "doc.insert", preview, EXIT_OK)
    if need_confirm and not args.yes:
        return _emit_preview(args, "doc.insert", preview, EXIT_NEEDS_CONFIRM,
                             note=_confirm_note(sensitive, destructive=False))
    backup_path = None
    if do_backup:
        backup_path = gws_safety.write_backup(doc_id, "doc-insert", {
            "revisionId": gws_docs.current_revision(creds, doc_id, rk),
            "index": index, "inserted_text": text, "markdown": markdown})
    if markdown:
        gws_docs.insert_markdown(creds, doc_id, index, text, resource_key=rk)
    else:
        gws_docs.insert_text(creds, doc_id, index, text, resource_key=rk)
    _journal("doc.insert", doc_id, {"index": index, "chars": len(text), "markdown": markdown}, backup_path)
    return _out(args, op="doc.insert", data={"id": doc_id, "index": index,
                "inserted_chars": len(text), "backup": backup_path})


def cmd_doc_replace(args):
    creds, doc_id, rk, local_path, gtype, info = _resolved(args, need_drive_meta=True, is_write=True)
    _require_type(gtype, "doc", "a .gdoc / document URL")
    before_text = gws_docs.plain_text(creds, doc_id, rk)  # one fetch: powers both the count and the backup
    matches = gws_docs.count_in_text(before_text, args.find, match_case=args.match_case)
    preview = (f"[replace] would replace {matches} occurrence(s) of {args.find!r} "
               f"with {args.replace!r} in doc {doc_id} (match_case={args.match_case}).")
    need_confirm, do_backup, sensitive = _gate(args, kind=DESTRUCTIVE, local_path=local_path, info=info)
    if args.dry_run:
        return _emit_preview(args, "doc.replace", preview, EXIT_OK)
    if need_confirm and not args.yes:
        return _emit_preview(args, "doc.replace", preview, EXIT_NEEDS_CONFIRM,
                             note=_confirm_note(sensitive, destructive=True))
    backup_path = None
    if do_backup:
        backup_path = gws_safety.write_backup(doc_id, "doc-replace", {
            "revisionId": gws_docs.current_revision(creds, doc_id, rk),
            "find": args.find, "replace": args.replace, "match_case": args.match_case,
            "occurrences": matches, "before_text": before_text})
    _, changed = gws_docs.replace_all(creds, doc_id, args.find, args.replace,
                                      match_case=args.match_case, resource_key=rk)
    _journal("doc.replace", doc_id, {"find": args.find, "replace": args.replace, "replaced": changed},
             backup_path)
    return _out(args, op="doc.replace", data={"id": doc_id, "replaced": changed, "backup": backup_path})


def cmd_doc_export(args):
    creds, doc_id, rk, _, gtype, info = _resolved(args, need_drive_meta=True)  # read-only op
    _require_type(gtype, "doc", "a .gdoc / document URL")
    mime = _DOC_EXPORT.get(args.export_as)
    if not mime:
        raise GwsError(f"unsupported doc export format: {args.export_as}",
                       remedy=f"Choose one of: {', '.join(_DOC_EXPORT)}.")
    out = args.out or _default_out((info or {}).get("name") or doc_id, args.export_as)
    export_file(creds, doc_id, mime, out, resource_key=rk)
    return _out(args, op="doc.export", data={"id": doc_id, "format": args.export_as, "out": out})


# --------------------------------------------------------------------------- sheets
def cmd_sheet_read(args):
    creds, sheet_id, rk, _, gtype, _ = _resolved(args)
    _require_type(gtype, "sheet", "a .gsheet / spreadsheet URL")
    render = gws_sheets.render_option(args.render)

    if args.meta or (not args.range and not args.tab and not args.ranges):
        meta = gws_sheets.metadata(creds, sheet_id, rk)
        content = _format_metadata(meta)
        return _out(args, op="sheet.read", content=content,
                    data={"id": sheet_id, "type": "sheet", "mode": "metadata"})

    from gws_render import sheet_values_to_text
    if args.ranges:
        vranges = gws_sheets.batch_get(creds, sheet_id, args.ranges, render=render, resource_key=rk)
        blocks = []
        for vr in vranges:
            text, _ = sheet_values_to_text(vr.get("values", []), args.format,
                                           max_rows=args.max_rows, max_cols=args.max_cols)
            blocks.append(f"## {vr.get('range')}\n{text}")
        content = "\n\n".join(blocks)
        return _out(args, op="sheet.read", content=content,
                    data={"id": sheet_id, "type": "sheet", "ranges": args.ranges})

    rng = args.range or args.tab
    values = gws_sheets.read_range(creds, sheet_id, rng, render=render, resource_key=rk)
    text, truncated = sheet_values_to_text(values, args.format, max_rows=args.max_rows, max_cols=args.max_cols)
    if truncated:
        text += f"\n\n> [truncated to {args.max_rows} rows x {args.max_cols} cols; narrow --range]"
    return _out(args, op="sheet.read", content=text,
                data={"id": sheet_id, "type": "sheet", "range": rng,
                      "rows": len(values), "truncated": truncated, "render": render})


def _format_metadata(meta):
    title = (meta.get("properties") or {}).get("title", "(untitled)")
    lines = [f"# {title}", "", "Tabs:"]
    for sh in meta.get("sheets", []):
        p = sh.get("properties", {})
        gp = p.get("gridProperties", {})
        lines.append(f"- {p.get('title')}  ({gp.get('rowCount', '?')} rows x {gp.get('columnCount', '?')} cols)")
    lines += ["", "Pass --tab NAME, --range A1:D10, or --ranges A1 B1 to read cells."]
    return "\n".join(lines)


def _sheet_before(creds, sheet_id, rng, rk):
    try:
        return gws_sheets.read_range(creds, sheet_id, rng, render="FORMULA", resource_key=rk)
    except GwsError:
        return []


def cmd_sheet_update(args):
    creds, sheet_id, rk, local_path, gtype, info = _resolved(args, need_drive_meta=True, is_write=True)
    _require_type(gtype, "sheet", "a .gsheet / spreadsheet URL")
    values = _parse_values(args)
    before = _sheet_before(creds, sheet_id, args.range, rk)
    preview = (f"[update] range {args.range} of sheet {sheet_id} (input={args.input})\n"
               f"BEFORE: {_short(before)}\nAFTER:  {_short(values)}")
    need_confirm, do_backup, sensitive = _gate(args, kind=DESTRUCTIVE, local_path=local_path, info=info)
    if args.dry_run:
        return _emit_preview(args, "sheet.update", preview, EXIT_OK)
    if need_confirm and not args.yes:
        return _emit_preview(args, "sheet.update", preview, EXIT_NEEDS_CONFIRM,
                             note=_confirm_note(sensitive, destructive=True))
    backup_path = None
    if do_backup:
        backup_path = gws_safety.write_backup(sheet_id, "sheet-update", {
            "range": args.range, "before_values": before, "value_input": args.input})
    input_opt = "RAW" if args.input == "raw" else "USER_ENTERED"
    result = gws_sheets.update(creds, sheet_id, args.range, values, value_input=input_opt, resource_key=rk)
    _journal("sheet.update", sheet_id, {"range": args.range, "cells": result.get("updatedCells")},
             backup_path)
    return _out(args, op="sheet.update", data={"id": sheet_id, "range": args.range,
                "updated_cells": result.get("updatedCells"), "backup": backup_path})


def cmd_sheet_append(args):
    creds, sheet_id, rk, local_path, gtype, info = _resolved(args, need_drive_meta=True, is_write=True)
    _require_type(gtype, "sheet", "a .gsheet / spreadsheet URL")
    values = _parse_values(args)
    need_confirm, do_backup, sensitive = _gate(args, kind=ADDITIVE, local_path=local_path, info=info)
    preview = (f"[append] would add {len(values)} row(s) to sheet {sheet_id} at {args.range} "
               f"(input={args.input}):\n{_short(values)}")
    if args.dry_run:
        return _emit_preview(args, "sheet.append", preview, EXIT_OK)
    if need_confirm and not args.yes:
        return _emit_preview(args, "sheet.append", preview, EXIT_NEEDS_CONFIRM,
                             note=_confirm_note(sensitive, destructive=False))
    backup_path = None
    if do_backup:
        backup_path = gws_safety.write_backup(sheet_id, "sheet-append", {
            "range": args.range, "appended_values": values})
    input_opt = "RAW" if args.input == "raw" else "USER_ENTERED"
    result = gws_sheets.append(creds, sheet_id, args.range, values, value_input=input_opt, resource_key=rk)
    updates = result.get("updates", {})
    _journal("sheet.append", sheet_id, {"range": args.range, "rows": len(values)}, backup_path)
    return _out(args, op="sheet.append", data={"id": sheet_id,
                "appended_range": updates.get("updatedRange"), "rows": len(values), "backup": backup_path})


def cmd_sheet_clear(args):
    creds, sheet_id, rk, local_path, gtype, info = _resolved(args, need_drive_meta=True, is_write=True)
    _require_type(gtype, "sheet", "a .gsheet / spreadsheet URL")
    before = _sheet_before(creds, sheet_id, args.range, rk)
    preview = (f"[clear] would clear range {args.range} of sheet {sheet_id} "
               f"({len(before)} row(s) currently):\n{_short(before)}")
    need_confirm, do_backup, sensitive = _gate(args, kind=DESTRUCTIVE, local_path=local_path, info=info)
    if args.dry_run:
        return _emit_preview(args, "sheet.clear", preview, EXIT_OK)
    if need_confirm and not args.yes:
        return _emit_preview(args, "sheet.clear", preview, EXIT_NEEDS_CONFIRM,
                             note=_confirm_note(sensitive, destructive=True))
    backup_path = None
    if do_backup:
        backup_path = gws_safety.write_backup(sheet_id, "sheet-clear", {
            "range": args.range, "before_values": before})
    result = gws_sheets.clear(creds, sheet_id, args.range, resource_key=rk)
    _journal("sheet.clear", sheet_id, {"range": args.range}, backup_path)
    return _out(args, op="sheet.clear", data={"id": sheet_id,
                "cleared_range": result.get("clearedRange"), "backup": backup_path})


def cmd_sheet_export(args):
    creds, sheet_id, rk, _, gtype, info = _resolved(args, need_drive_meta=True)  # read-only op
    _require_type(gtype, "sheet", "a .gsheet / spreadsheet URL")
    mime = _SHEET_EXPORT.get(args.export_as)
    if not mime:
        raise GwsError(f"unsupported sheet export format: {args.export_as}",
                       remedy=f"Choose one of: {', '.join(_SHEET_EXPORT)}.")
    out = args.out or _default_out((info or {}).get("name") or sheet_id, args.export_as)
    export_file(creds, sheet_id, mime, out, resource_key=rk)
    note = "csv/tsv export covers the first sheet only" if args.export_as in ("csv", "tsv") else ""
    return _out(args, op="sheet.export", data={"id": sheet_id, "format": args.export_as,
                "out": out, "note": note})


def cmd_sheet_batch_update(args):
    creds, sheet_id, rk, local_path, gtype, info = _resolved(args, need_drive_meta=True, is_write=True)
    _require_type(gtype, "sheet", "a .gsheet / spreadsheet URL")
    data = _parse_batch_data(args)
    ranges = [d["range"] for d in data]
    before = {rng: _sheet_before(creds, sheet_id, rng, rk) for rng in ranges}
    preview = (f"[batch-update] would write {len(data)} range(s) of sheet {sheet_id} "
               f"(input={args.input}):\n{_short({d['range']: d['values'] for d in data})}")
    need_confirm, do_backup, sensitive = _gate(args, kind=DESTRUCTIVE, local_path=local_path, info=info)
    if args.dry_run:
        return _emit_preview(args, "sheet.batch_update", preview, EXIT_OK)
    if need_confirm and not args.yes:
        return _emit_preview(args, "sheet.batch_update", preview, EXIT_NEEDS_CONFIRM,
                             note=_confirm_note(sensitive, destructive=True))
    backup_path = None
    if do_backup:
        backup_path = gws_safety.write_backup(sheet_id, "sheet-batch-update",
                                              {"before_values_by_range": before})
    input_opt = "RAW" if args.input == "raw" else "USER_ENTERED"
    result = gws_sheets.batch_update_values(creds, sheet_id, data, value_input=input_opt, resource_key=rk)
    _journal("sheet.batch_update", sheet_id, {"ranges": ranges}, backup_path)
    return _out(args, op="sheet.batch_update", data={"id": sheet_id, "ranges": ranges,
                "updated_cells": result.get("totalUpdatedCells"), "backup": backup_path})


def _parse_batch_data(args):
    try:
        data = json.loads(args.data_json)
    except Exception as exc:
        raise GwsError(f"--data-json is not valid JSON ({exc})",
                       remedy='e.g. [{"range":"S!A1","values":[["x"]]},{"range":"S!B1","values":[["y"]]}]')
    if not (isinstance(data, list) and data
            and all(isinstance(d, dict) and "range" in d and "values" in d for d in data)):
        raise GwsError("--data-json must be a non-empty JSON array of {range, values} objects",
                       remedy='e.g. [{"range":"S!A1","values":[["x"]]}]')
    return data


# --------------------------------------------------------------------------- restore
def cmd_restore(args):
    """Revert a prior write from its secrets/backups snapshot. Covers the destructive ops whose
    before-image is recoverable: sheet-update / sheet-clear (write the values back) and doc-replace
    (best-effort reverse replace). Additive ops (append/insert) have no clean automatic undo."""
    data = gws_safety.load_backup(args.backup_file)
    op, file_id, ts = data.get("op"), data.get("id"), data.get("ts")
    creds = resolve_credentials("write")
    _assert_account(args, None, creds, is_write=True)  # honor --account even though there's no stub
    rk = getattr(args, "resource_key", None) or ""

    if op in ("sheet-update", "sheet-clear"):
        rng, before = data.get("range"), (data.get("before_values") or [])
        preview = (f"[restore] would put {len(before)} row(s) back into {rng} of sheet {file_id} "
                   f"(from {op} backup {ts}):\n{_short(before)}")
        if args.dry_run:
            return _emit_preview(args, "restore", preview, EXIT_OK)
        if not args.yes:
            return _emit_preview(args, "restore", preview, EXIT_NEEDS_CONFIRM,
                                 note="Destructive: re-run with --yes to apply the restore.")
        # before_values were captured with the FORMULA render, i.e. exactly what a user would type,
        # so USER_ENTERED (not RAW) faithfully reconstructs formulas, numbers, and dates.
        if before:
            gws_sheets.update(creds, file_id, rng, before, value_input="USER_ENTERED", resource_key=rk)
        else:
            gws_sheets.clear(creds, file_id, rng, resource_key=rk)  # range was empty before
        _journal("restore", file_id, {"from_op": op, "range": rng, "rows": len(before)}, args.backup_file)
        return _out(args, op="restore", data={"id": file_id, "restored_range": rng, "rows": len(before)})

    if op == "sheet-batch-update":
        by_range = data.get("before_values_by_range") or {}
        preview = (f"[restore] would put {len(by_range)} range(s) back into sheet {file_id} "
                   f"(from {op} backup {ts}):\n{_short(by_range)}")
        if args.dry_run:
            return _emit_preview(args, "restore", preview, EXIT_OK)
        if not args.yes:
            return _emit_preview(args, "restore", preview, EXIT_NEEDS_CONFIRM,
                                 note="Destructive: re-run with --yes to apply the restore.")
        restore_rows = [{"range": rng, "values": before} for rng, before in by_range.items() if before]
        if restore_rows:
            gws_sheets.batch_update_values(creds, file_id, restore_rows,
                                           value_input="USER_ENTERED", resource_key=rk)
        for rng, before in by_range.items():
            if not before:
                gws_sheets.clear(creds, file_id, rng, resource_key=rk)
        _journal("restore", file_id, {"from_op": op, "ranges": list(by_range)}, args.backup_file)
        return _out(args, op="restore", data={"id": file_id, "restored_ranges": list(by_range)})

    if op == "doc-replace":
        find, replace = data.get("find"), data.get("replace")
        mc = data.get("match_case", True)
        preview = (f"[restore] would reverse the replace on doc {file_id}: put {find!r} back where "
                   f"{replace!r} now stands (match_case={mc}, backup {ts}).")
        if args.dry_run:
            return _emit_preview(args, "restore", preview, EXIT_OK)
        if not args.yes:
            return _emit_preview(args, "restore", preview, EXIT_NEEDS_CONFIRM,
                                 note="Best-effort reverse replace (clean only if the replacement string "
                                      "was unique). Re-run with --yes to apply.")
        _, changed = gws_docs.replace_all(creds, file_id, replace, find, match_case=mc, resource_key=rk)
        _journal("restore", file_id, {"from_op": op, "reversed": changed}, args.backup_file)
        return _out(args, op="restore", data={"id": file_id, "reversed_occurrences": changed})

    raise GwsError(f"restore is not supported for a {op!r} backup",
                   remedy="Automatic restore covers sheet-update, sheet-clear, and doc-replace. "
                          "Additive ops (append/insert) have no clean undo - edit manually.")


# --------------------------------------------------------------------------- argparse
def _add_common(p):
    p.add_argument("--json", action="store_true", help="machine-readable JSON envelope")
    p.add_argument("--account", help="assert the authenticated account email")
    p.add_argument("--resource-key", help="resource key for a link-shared file")
    p.add_argument("--verbose", action="store_true")


def build_parser():
    parser = argparse.ArgumentParser(prog="gws.py", description="Read/write Google Docs & Sheets.")
    groups = parser.add_subparsers(dest="group", required=True)

    # auth
    auth = groups.add_parser("auth", help="authentication").add_subparsers(dest="cmd", required=True)
    a_login = auth.add_parser("login", help="one-time consent -> secrets/token.json")
    a_login.add_argument("--scopes", choices=["read", "write"], default="write")
    a_login.add_argument("--force", action="store_true", help="discard existing token first")
    _add_common(a_login); a_login.set_defaults(func=cmd_auth_login)
    a_status = auth.add_parser("status", help="show active credential")
    a_status.add_argument("--scopes", choices=["read", "write"], default="write")
    _add_common(a_status); a_status.set_defaults(func=cmd_auth_status)

    # resolve
    r = groups.add_parser("resolve", help="stub/URL/id -> id, type, name")
    r.add_argument("target")
    _add_common(r); r.set_defaults(func=cmd_resolve)

    # restore
    rst = groups.add_parser("restore", help="revert a prior write from its secrets/backups snapshot")
    rst.add_argument("backup_file", help="path to a secrets/backups/*.json snapshot")
    rst.add_argument("--dry-run", action="store_true"); rst.add_argument("--yes", action="store_true")
    _add_common(rst); rst.set_defaults(func=cmd_restore)

    # doc
    doc = groups.add_parser("doc", help="Google Docs").add_subparsers(dest="cmd", required=True)
    d_read = doc.add_parser("read", help="render a Doc")
    d_read.add_argument("target")
    d_read.add_argument("--format", choices=["markdown", "text", "json"], default="markdown")
    d_read.add_argument("--section", help="only the section under this heading")
    d_read.add_argument("--tab-id", help="only this tab (defaults to the tab in the URL, if any)")
    d_read.add_argument("--max-chars", type=int, default=40000)
    d_read.add_argument("--include-title", action=argparse.BooleanOptionalAction, default=True)
    _add_common(d_read); d_read.set_defaults(func=cmd_doc_read)
    d_app = doc.add_parser("append", help="append text (or --markdown) to body end")
    d_app.add_argument("target"); d_app.add_argument("--text"); d_app.add_argument("--text-file")
    d_app.add_argument("--markdown", action="store_true",
                       help="interpret the text as Markdown (headings, bold/italic, lists, links)")
    d_app.add_argument("--dry-run", action="store_true"); d_app.add_argument("--yes", action="store_true")
    d_app.add_argument("--backup", action="store_true")
    _add_common(d_app); d_app.set_defaults(func=cmd_doc_append)
    d_ins = doc.add_parser("insert", help="insert text at a UTF-16 index or a text/heading anchor")
    d_ins.add_argument("target"); d_ins.add_argument("--text"); d_ins.add_argument("--text-file")
    d_ins.add_argument("--markdown", action="store_true", help="interpret the text as Markdown")
    d_ins.add_argument("--index", type=int, help="UTF-16 index to insert at")
    d_ins.add_argument("--after-heading", help="insert just after the paragraph with this heading text")
    d_ins.add_argument("--after-text", help="insert just after the first occurrence of this text")
    d_ins.add_argument("--at-start", action="store_true", help="insert at the very start of the body")
    d_ins.add_argument("--tab-id", help="target tab (defaults to the URL's tab, else the first tab)")
    d_ins.add_argument("--dry-run", action="store_true"); d_ins.add_argument("--yes", action="store_true")
    d_ins.add_argument("--backup", action="store_true")
    _add_common(d_ins); d_ins.set_defaults(func=cmd_doc_insert)
    d_rep = doc.add_parser("replace", help="find-and-replace all (destructive)")
    d_rep.add_argument("target"); d_rep.add_argument("--find", required=True); d_rep.add_argument("--replace", required=True)
    d_rep.add_argument("--match-case", action=argparse.BooleanOptionalAction, default=True)
    d_rep.add_argument("--dry-run", action="store_true"); d_rep.add_argument("--yes", action="store_true")
    d_rep.add_argument("--backup", action="store_true")
    _add_common(d_rep); d_rep.set_defaults(func=cmd_doc_replace)
    d_exp = doc.add_parser("export", help="download a Doc as pdf/docx/txt/md/html")
    d_exp.add_argument("target")
    d_exp.add_argument("--as", dest="export_as", required=True, choices=list(_DOC_EXPORT),
                       help="export format")
    d_exp.add_argument("--out", help="output path (default: <name>.<ext>)")
    _add_common(d_exp); d_exp.set_defaults(func=cmd_doc_export)

    # sheet
    sheet = groups.add_parser("sheet", help="Google Sheets").add_subparsers(dest="cmd", required=True)
    s_read = sheet.add_parser("read", help="read tabs+dims, or a range")
    s_read.add_argument("target")
    s_read.add_argument("--tab"); s_read.add_argument("--range"); s_read.add_argument("--ranges", nargs="+")
    s_read.add_argument("--format", choices=["markdown", "csv", "tsv", "json"], default="markdown")
    s_read.add_argument("--render", choices=["formatted", "unformatted", "formula"], default="formatted")
    s_read.add_argument("--max-rows", type=int, default=200); s_read.add_argument("--max-cols", type=int, default=40)
    s_read.add_argument("--meta", action="store_true")
    _add_common(s_read); s_read.set_defaults(func=cmd_sheet_read)
    s_upd = sheet.add_parser("update", help="overwrite a range (destructive)")
    s_upd.add_argument("target"); s_upd.add_argument("--range", required=True)
    s_upd.add_argument("--values-json"); s_upd.add_argument("--values-csv")
    s_upd.add_argument("--input", choices=["raw", "user"], default="user")
    s_upd.add_argument("--dry-run", action="store_true"); s_upd.add_argument("--yes", action="store_true")
    s_upd.add_argument("--backup", action="store_true")
    _add_common(s_upd); s_upd.set_defaults(func=cmd_sheet_update)
    s_app = sheet.add_parser("append", help="append rows (INSERT_ROWS)")
    s_app.add_argument("target"); s_app.add_argument("--range", required=True)
    s_app.add_argument("--values-json"); s_app.add_argument("--values-csv")
    s_app.add_argument("--input", choices=["raw", "user"], default="user")
    s_app.add_argument("--dry-run", action="store_true"); s_app.add_argument("--yes", action="store_true")
    s_app.add_argument("--backup", action="store_true")
    _add_common(s_app); s_app.set_defaults(func=cmd_sheet_append)
    s_clr = sheet.add_parser("clear", help="clear a range (destructive)")
    s_clr.add_argument("target"); s_clr.add_argument("--range", required=True)
    s_clr.add_argument("--dry-run", action="store_true"); s_clr.add_argument("--yes", action="store_true")
    s_clr.add_argument("--backup", action="store_true")
    _add_common(s_clr); s_clr.set_defaults(func=cmd_sheet_clear)
    s_bu = sheet.add_parser("batch-update", help="atomically overwrite several ranges (destructive)")
    s_bu.add_argument("target")
    s_bu.add_argument("--data-json", required=True,
                      help='JSON array of {range, values}, e.g. [{"range":"S!A1","values":[["x"]]}]')
    s_bu.add_argument("--input", choices=["raw", "user"], default="user")
    s_bu.add_argument("--dry-run", action="store_true"); s_bu.add_argument("--yes", action="store_true")
    s_bu.add_argument("--backup", action="store_true")
    _add_common(s_bu); s_bu.set_defaults(func=cmd_sheet_batch_update)
    s_exp = sheet.add_parser("export", help="download a Sheet as xlsx/csv/tsv/pdf")
    s_exp.add_argument("target")
    s_exp.add_argument("--as", dest="export_as", required=True, choices=list(_SHEET_EXPORT),
                       help="export format (csv/tsv = first sheet only)")
    s_exp.add_argument("--out", help="output path (default: <name>.<ext>)")
    _add_common(s_exp); s_exp.set_defaults(func=cmd_sheet_export)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not hasattr(args, "json"):
        args.json = False
    try:
        return args.func(args)
    except AuthError as exc:
        return _emit_error(args, GwsError(str(exc), exit_code=2,
                                          remedy="Run: uv run --script gws.py auth login"))
    except GwsError as exc:
        return _emit_error(args, exc)
    except BrokenPipeError:
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
