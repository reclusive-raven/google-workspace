"""Resolve a Google Doc/Sheet target to a document id.

A target is one of:
  - a local .gdoc/.gsheet stub file path (JSON with a `doc_id` + optional `resource_key`),
  - a docs.google.com / drive.google.com URL,
  - a raw document id.

parse_target() is pure-local and enough to address Docs/Sheets (they key off the document id).
resolve() additionally hits the Drive API to enrich (name, mimeType, driveId, resourceKey) and to
follow shortcuts - used by the `resolve` command and when the caller wants the real type/name.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from gws_http import GwsError, EXIT_GENERIC, execute

_STUB_EXTS = {".gdoc", ".gsheet", ".gslides", ".gform", ".gdraw", ".gmap", ".gsite", ".gtable", ".gjam"}

_EXT_TYPE = {
    ".gdoc": "doc", ".gsheet": "sheet", ".gslides": "slides", ".gform": "form",
    ".gdraw": "drawing", ".gmap": "map", ".gsite": "site", ".gtable": "table", ".gjam": "jam",
}

_MIME_TYPE = {
    "application/vnd.google-apps.document": "doc",
    "application/vnd.google-apps.spreadsheet": "sheet",
    "application/vnd.google-apps.presentation": "slides",
    "application/vnd.google-apps.form": "form",
    "application/vnd.google-apps.drawing": "drawing",
    "application/vnd.google-apps.map": "map",
    "application/vnd.google-apps.site": "site",
    "application/vnd.google-apps.shortcut": "shortcut",
}

SUPPORTED = {"doc", "sheet"}  # types this skill can read/write natively

_URL_ID_RE = re.compile(r"/(?:document|spreadsheets|presentation|forms|file)/d/([A-Za-z0-9_-]+)")
_URL_QUERY_ID_RE = re.compile(r"[?&]id=([A-Za-z0-9_-]+)")
_RESOURCE_KEY_RE = re.compile(r"[?&]resourcekey=([A-Za-z0-9_-]+)")
_TAB_RE = re.compile(r"[?&]tab=([A-Za-z0-9._-]+)")
_URL_TYPE_RE = re.compile(r"/(document|spreadsheets|presentation|forms)/d/")
_BARE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,}$")
_SHORTCUT_TARGET_RE = re.compile(r"/\.shortcut-targets-by-id/([A-Za-z0-9_-]+)/")
_URL_PATH_TYPE = {"document": "doc", "spreadsheets": "sheet", "presentation": "slides", "forms": "form"}


def _bad(target, remedy):
    return GwsError(f"unrecognized target: {target}", exit_code=EXIT_GENERIC, remedy=remedy)


def parse_target(target: str) -> dict:
    """Local-only parse. Returns {id, resource_key, hint_type, local_path}."""
    t = target.strip()

    # 1. local stub file
    p = Path(t).expanduser()
    if p.exists() and p.is_file():
        return _parse_stub(p)

    # a non-stub path under .shortcut-targets-by-id/<id>/ -> use the id
    m = _SHORTCUT_TARGET_RE.search(t)
    if m and p.suffix.lower() not in _STUB_EXTS:
        return {"id": m.group(1), "resource_key": "", "hint_type": None, "local_path": None}

    # 2. URL
    if "google.com" in t or t.startswith("http"):
        rid = _URL_ID_RE.search(t) or _URL_QUERY_ID_RE.search(t)
        if not rid:
            raise _bad(target, "Could not extract a document id from that URL.")
        rk = _RESOURCE_KEY_RE.search(t)
        tm = _URL_TYPE_RE.search(t)
        tab = _TAB_RE.search(t)
        hint = _URL_PATH_TYPE.get(tm.group(1)) if tm else None
        return {"id": rid.group(1), "resource_key": rk.group(1) if rk else "",
                "hint_type": hint, "local_path": None, "tab_id": tab.group(1) if tab else None}

    # 3. bare id
    if _BARE_ID_RE.match(t):
        return {"id": t, "resource_key": "", "hint_type": None, "local_path": None}

    raise _bad(target, "Pass a .gdoc/.gsheet stub path, a docs.google.com URL, or a raw document id.")


def _parse_stub(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except Exception as exc:
        raise GwsError(f"not a readable Google stub file: {path} ({exc})", exit_code=EXIT_GENERIC,
                       remedy="Expected a .gdoc/.gsheet JSON stub containing a doc_id.")
    doc_id = data.get("doc_id")
    if not doc_id:
        raise GwsError(f"stub has no doc_id: {path}", exit_code=EXIT_GENERIC,
                       remedy="This file does not look like a Google Drive stub.")
    return {
        "id": doc_id,
        "resource_key": data.get("resource_key") or "",
        "hint_type": _EXT_TYPE.get(path.suffix.lower()),
        "local_path": str(path),
        "stub_email": data.get("email"),
    }


def resolve(creds, target, *, resource_key_override=None, follow_shortcut=True) -> dict:
    """Full resolve with Drive enrichment. Returns id/type/name/driveId/resourceKey/isShortcut/
    local_path/stub_email plus a sensitivity verdict (sensitive/folder_path) derived from the file's
    Drive ancestors - so a target reached by URL/id is classified the same as one reached by its stub."""
    from gws_auth import drive_service

    parsed = parse_target(target)
    resource_key = resource_key_override or parsed["resource_key"]
    drive = drive_service(creds)
    info = _drive_get(drive, parsed["id"], resource_key)

    result = {
        "id": info["id"],
        "type": _MIME_TYPE.get(info.get("mimeType"), "binary"),
        "name": info.get("name"),
        "driveId": info.get("driveId"),
        "resourceKey": info.get("resourceKey") or resource_key or "",
        "isShortcut": False,
        "mimeType": info.get("mimeType"),
        "local_path": parsed.get("local_path"),
        "stub_email": parsed.get("stub_email"),
    }
    final_parents = info.get("parents")

    if result["type"] == "shortcut" and follow_shortcut:
        target_id = (info.get("shortcutDetails") or {}).get("targetId")
        if target_id:
            tinfo = _drive_get(drive, target_id, "")
            result.update({
                "id": tinfo["id"],
                "type": _MIME_TYPE.get(tinfo.get("mimeType"), "binary"),
                "name": tinfo.get("name"),
                "driveId": tinfo.get("driveId"),
                "resourceKey": tinfo.get("resourceKey") or "",
                "isShortcut": True,
                "mimeType": tinfo.get("mimeType"),
            })
            final_parents = tinfo.get("parents")

    result.update(_sensitivity(drive, final_parents, result.get("local_path")))
    return result


def _sensitivity(drive, parents, local_path):
    """Verdict for a target: sensitive if its stub path OR any Drive ancestor folder is sensitive.
    Fail-safe - if parents can't be read, keep the local verdict and flag it unverified."""
    from gws_safety import classify_sensitivity

    names = ancestor_folder_names(drive, parents)
    folder_path = "/".join(reversed(names)) if names else None  # root-first, for display
    by_folder = classify_sensitivity("/".join(names)) == "sensitive"
    by_local = classify_sensitivity(local_path) == "sensitive"
    unverified = not names and not local_path  # URL/id target whose ancestors we couldn't read
    return {"sensitive": by_folder or by_local, "folder_path": folder_path,
            "sensitivity_unverified": unverified}


def ancestor_folder_names(drive, parents, cap=10):
    """Folder names above a file, nearest-first. Best-effort: returns [] if parents can't be read.
    Walks up to `cap` levels and de-dupes to avoid loops in pathological parent graphs."""
    names, seen, queue = [], set(), list(parents or [])
    for _ in range(cap):
        if not queue:
            break
        pid = queue.pop(0)
        if pid in seen:
            continue
        seen.add(pid)
        try:
            pmeta = drive.files().get(
                fileId=pid, supportsAllDrives=True, fields="id,name,parents").execute()
        except Exception:
            break
        if pmeta.get("name"):
            names.append(pmeta["name"])
        queue = (pmeta.get("parents") or []) + queue  # continue upward toward the root
    return names


def export_file(creds, file_id, export_mime, out_path, *, resource_key=""):
    """Export a Google-native Doc/Sheet to a binary file (pdf/docx/xlsx/csv/...) via Drive export."""
    import io
    from googleapiclient.http import MediaIoBaseDownload
    from gws_auth import drive_service

    drive = drive_service(creds)
    req = drive.files().export_media(fileId=file_id, mimeType=export_mime)
    if resource_key:
        req.headers["X-Goog-Drive-Resource-Keys"] = f"{file_id}/{resource_key}"
    buf = io.FileIO(out_path, "wb")
    downloader = MediaIoBaseDownload(buf, req)
    done = False
    try:
        while not done:
            _, done = downloader.next_chunk()
    finally:
        buf.close()
    return out_path


def _drive_get(drive, file_id, resource_key):
    req = drive.files().get(
        fileId=file_id, supportsAllDrives=True,
        fields="id,name,mimeType,driveId,resourceKey,parents,"
               "shortcutDetails(targetId,targetMimeType)")
    return execute(req, file_id=file_id, resource_key=resource_key)
