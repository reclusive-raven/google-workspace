"""Google Docs read + write operations.

Writes obtain the current revisionId immediately before batchUpdate and pass it as
writeControl.requiredRevisionId, so a concurrent edit fails loudly (exit 7) instead of silently
clobbering someone else's change.
"""
from __future__ import annotations

import json

from gws_auth import docs_service
from gws_http import GwsError, execute
import gws_md2docs
import gws_render

# With includeTabsContent, the legacy top-level body/lists/inlineObjects fields are disallowed in the
# mask - each tab carries its own body/lists/inlineObjects under tabs[].documentTab.
_READ_FIELDS = "title,revisionId,tabs"


def get_document(creds, doc_id, resource_key=""):
    svc = docs_service(creds)
    req = svc.documents().get(documentId=doc_id, includeTabsContent=True, fields=_READ_FIELDS)
    return execute(req, file_id=doc_id, resource_key=resource_key)


def read(creds, doc_id, *, resource_key="", fmt="markdown", section=None,
         max_chars=40000, include_title=True, tab_id=None):
    doc = get_document(creds, doc_id, resource_key)
    meta = {"revisionId": doc.get("revisionId"), "title": doc.get("title"), "truncated": False}
    if fmt == "json":
        return json.dumps(doc, ensure_ascii=False), meta
    md = gws_render.doc_to_markdown(doc, include_title=include_title, plain=(fmt == "text"), tab_id=tab_id)
    if section:
        sliced = gws_render.slice_section(md, section)
        if sliced is None:
            from gws_http import GwsError
            raise GwsError(f"section not found: {section!r}",
                           remedy="Check the heading text (matching is case-insensitive).")
        md = sliced
    if max_chars:
        new = gws_render.truncate(md, max_chars)
        meta["truncated"] = new != md
        md = new
    return md, meta


def _utf16_len(s: str) -> int:
    """Length in UTF-16 code units - the unit Docs indices are measured in (not Python code points)."""
    return len(s.encode("utf-16-le")) // 2


def _para_plain(para: dict) -> str:
    return "".join((e.get("textRun", {}) or {}).get("content", "") or "" for e in para.get("elements", []))


def _tab_bodies(doc: dict, tab_id):
    """The body dict(s) for the addressed tab, reusing the renderer's tab selection so anchors and
    rendering always agree on which tab is 'the' tab."""
    tabs = gws_render._collect_tabs(doc)              # [(title, body, lists, objects, tabId)]
    if tab_id:
        matched = gws_render._match_tabs(tabs, tab_id)
        if matched:
            tabs = matched
    return [t[1] for t in tabs]


def find_index(creds, doc_id, *, resource_key="", tab_id=None,
               after_heading=None, after_text=None, at_start=False):
    """Resolve an insertion anchor to a concrete UTF-16 index within the addressed tab's body.

    - at_start      -> the first insertable index (usually 1)
    - after_heading -> just past the paragraph whose heading/title text matches (case-insensitive)
    - after_text    -> just past the first occurrence of the literal text (case-sensitive)
    """
    doc = get_document(creds, doc_id, resource_key)
    bodies = _tab_bodies(doc, tab_id)
    if not bodies:
        raise GwsError("could not read the document body",
                       remedy="The doc may be empty or the tab id may be wrong.")
    content = bodies[0].get("content", [])

    if at_start:
        for el in content:
            if "paragraph" in el and "startIndex" in el:
                return el["startIndex"]
        return 1

    if after_heading is not None:
        target = after_heading.strip().lower()
        for el in content:
            para = el.get("paragraph")
            if not para:
                continue
            style = (para.get("paragraphStyle") or {}).get("namedStyleType", "")
            if not (style.startswith("HEADING_") or style == "TITLE"):
                continue
            if _para_plain(para).strip().lower() == target:
                return el.get("endIndex")            # boundary right after the heading paragraph
        raise GwsError(f"heading not found: {after_heading!r}",
                       remedy="Check the heading text (matching is case-insensitive).")

    if after_text is not None:
        for el in content:
            para = el.get("paragraph")
            if not para:
                continue
            for e in para.get("elements", []):
                if "textRun" not in e or "startIndex" not in e:
                    continue
                s = e["textRun"].get("content", "") or ""
                pos = s.find(after_text)
                if pos != -1:
                    return e["startIndex"] + _utf16_len(s[:pos + len(after_text)])
        raise GwsError(f"text not found: {after_text!r}",
                       remedy="Check the exact text (matching is case-sensitive).")

    raise GwsError("no insertion anchor given",
                   remedy="Pass one of --after-heading / --after-text / --at-start.")


def current_revision(creds, doc_id, resource_key=""):
    svc = docs_service(creds)
    req = svc.documents().get(documentId=doc_id, fields="revisionId")
    return execute(req, file_id=doc_id, resource_key=resource_key).get("revisionId")


def plain_text(creds, doc_id, resource_key=""):
    doc = get_document(creds, doc_id, resource_key)
    return gws_render.doc_to_markdown(doc, include_title=True, plain=True)


def _batch_update(creds, doc_id, requests, resource_key="", required_revision_id=None):
    svc = docs_service(creds)
    body = {"requests": requests}
    if required_revision_id:
        body["writeControl"] = {"requiredRevisionId": required_revision_id}
    req = svc.documents().batchUpdate(documentId=doc_id, body=body)
    return execute(req, file_id=doc_id, resource_key=resource_key)


def append_text(creds, doc_id, text, *, resource_key=""):
    rev = current_revision(creds, doc_id, resource_key)
    requests = [{"insertText": {"text": text, "endOfSegmentLocation": {}}}]
    return _batch_update(creds, doc_id, requests, resource_key, rev)


def insert_text(creds, doc_id, index, text, *, resource_key=""):
    rev = current_revision(creds, doc_id, resource_key)
    requests = [{"insertText": {"text": text, "location": {"index": index}}}]
    return _batch_update(creds, doc_id, requests, resource_key, rev)


def append_markdown(creds, doc_id, md, *, resource_key=""):
    """Append Markdown (headings/bold/lists/links) at the end of the first tab's body."""
    doc = get_document(creds, doc_id, resource_key)
    bodies = _tab_bodies(doc, None)
    content = bodies[0].get("content", []) if bodies else []
    end = content[-1].get("endIndex", 2) if content else 2
    start_index = max(1, end - 1)                       # just before the body's final newline
    requests = gws_md2docs.markdown_to_requests("\n" + md, start_index=start_index)  # blank line = separator
    rev = current_revision(creds, doc_id, resource_key)
    return _batch_update(creds, doc_id, requests, resource_key, rev)


def insert_markdown(creds, doc_id, index, md, *, resource_key=""):
    """Insert Markdown at a UTF-16 index (see find_index for anchor resolution)."""
    requests = gws_md2docs.markdown_to_requests(md, start_index=index)
    rev = current_revision(creds, doc_id, resource_key)
    return _batch_update(creds, doc_id, requests, resource_key, rev)


def replace_all(creds, doc_id, find, replace, *, match_case=True, resource_key=""):
    rev = current_revision(creds, doc_id, resource_key)
    requests = [{"replaceAllText": {
        "containsText": {"text": find, "matchCase": match_case}, "replaceText": replace}}]
    result = _batch_update(creds, doc_id, requests, resource_key, rev)
    changed = sum((r.get("replaceAllText") or {}).get("occurrencesChanged", 0)
                  for r in result.get("replies", []))
    return result, changed


def count_in_text(text, find, *, match_case=True):
    """Count occurrences of `find` in already-fetched text (used for replace previews)."""
    hay = text if match_case else text.lower()
    needle = find if match_case else find.lower()
    return hay.count(needle) if needle else 0


def count_matches(creds, doc_id, find, *, match_case=True, resource_key=""):
    """Count occurrences for a dry-run preview (approximate: over rendered plain text)."""
    return count_in_text(plain_text(creds, doc_id, resource_key), find, match_case=match_case)
