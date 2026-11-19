"""Markdown (a documented subset) -> Google Docs batchUpdate requests.

The inverse of gws_render's Docs->Markdown. Supported subset:
  - headings   # .. ######            -> namedStyleType HEADING_1..6
  - paragraphs and blank lines
  - unordered lists  -,*,+            -> createParagraphBullets (disc)
  - ordered lists    1. / 1)          -> createParagraphBullets (decimal); consecutive items share a
                                         list so numbering is continuous
  - inline **bold**, *italic*/_it_, ***both***, `code`, [text](url)
NOT supported (write side): tables and images. Reads still render those; writes decline silently by
treating such lines as plain paragraphs. Nested list levels render flat (single level) in v1.

Index strategy: everything is emitted as ONE batch whose first request inserts the whole plain text at
`start_index`; every later styling request addresses absolute positions `start_index + offset`. Because
batchUpdate applies requests in order, the styling sees the already-inserted text. None of the styling
requests (updateParagraphStyle / updateTextStyle / createParagraphBullets with no leading tabs) shift
indices, so the offsets stay valid. Offsets are UTF-16 code units - the unit Docs indices use.
"""
from __future__ import annotations

import re

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_UL_RE = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_OL_RE = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")

_INLINE = re.compile(
    r"\*\*\*(?P<bi>.+?)\*\*\*"
    r"|\*\*(?P<b>.+?)\*\*"
    r"|__(?P<b2>.+?)__"
    r"|\*(?P<i>.+?)\*"
    r"|_(?P<i2>.+?)_"
    r"|\[(?P<lt>[^\]]+)\]\((?P<lu>[^)]+)\)"
    r"|`(?P<code>[^`]+)`"
)


def _u16(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def parse_inline(s: str):
    """Return (clean_text, spans). Each span = {start, end, bold?/italic?/link/code} with start/end as
    UTF-16 offsets into clean_text."""
    out, spans, pos, idx = [], [], 0, 0
    for m in _INLINE.finditer(s):
        if m.start() > pos:
            lit = s[pos:m.start()]
            out.append(lit); idx += _u16(lit)
        if m.group("bi") is not None:
            c = m.group("bi"); start = idx; out.append(c); idx += _u16(c)
            spans.append({"start": start, "end": idx, "bold": True, "italic": True})
        elif m.group("b") is not None or m.group("b2") is not None:
            c = m.group("b") if m.group("b") is not None else m.group("b2")
            start = idx; out.append(c); idx += _u16(c)
            spans.append({"start": start, "end": idx, "bold": True})
        elif m.group("i") is not None or m.group("i2") is not None:
            c = m.group("i") if m.group("i") is not None else m.group("i2")
            start = idx; out.append(c); idx += _u16(c)
            spans.append({"start": start, "end": idx, "italic": True})
        elif m.group("lt") is not None:
            c = m.group("lt"); start = idx; out.append(c); idx += _u16(c)
            spans.append({"start": start, "end": idx, "link": m.group("lu")})
        elif m.group("code") is not None:
            c = m.group("code"); start = idx; out.append(c); idx += _u16(c)
            spans.append({"start": start, "end": idx, "code": True})
        pos = m.end()
    if pos < len(s):
        lit = s[pos:]; out.append(lit); idx += _u16(lit)
    return "".join(out), spans


def _parse_blocks(md: str):
    blocks = []
    for line in md.split("\n"):
        mh = _HEADING_RE.match(line)
        mu = _UL_RE.match(line)
        mo = _OL_RE.match(line)
        if mh:
            blocks.append({"kind": "heading", "level": len(mh.group(1)), "text": mh.group(2)})
        elif mu:
            blocks.append({"kind": "ul", "text": mu.group(2)})
        elif mo:
            blocks.append({"kind": "ol", "text": mo.group(2)})
        else:
            blocks.append({"kind": "p", "text": line})
    return blocks


def markdown_to_requests(md: str, *, start_index: int):
    """Build the batchUpdate request list that writes `md` starting at UTF-16 index `start_index`."""
    blocks = _parse_blocks(md)
    pieces, metas, cursor = [], [], 0
    for b in blocks:
        text, spans = parse_inline(b["text"])
        line = text + "\n"
        metas.append({
            "text_start": start_index + cursor,
            "para_end": start_index + cursor + _u16(line),
            "block": b, "spans": spans, "text": text,
        })
        pieces.append(line)
        cursor += _u16(line)

    full = "".join(pieces)
    requests = [{"insertText": {"text": full, "location": {"index": start_index}}}]

    # paragraph styles: headings
    for meta in metas:
        b = meta["block"]
        if b["kind"] == "heading":
            requests.append({"updateParagraphStyle": {
                "range": {"startIndex": meta["text_start"], "endIndex": meta["para_end"]},
                "paragraphStyle": {"namedStyleType": f"HEADING_{b['level']}"},
                "fields": "namedStyleType"}})

    # bullets: coalesce consecutive same-kind list items into one list (continuous numbering)
    i = 0
    while i < len(metas):
        kind = metas[i]["block"]["kind"]
        if kind in ("ul", "ol"):
            j = i
            while j + 1 < len(metas) and metas[j + 1]["block"]["kind"] == kind:
                j += 1
            preset = "BULLET_DISC_CIRCLE_SQUARE" if kind == "ul" else "NUMBERED_DECIMAL_ALPHA_ROMAN"
            requests.append({"createParagraphBullets": {
                "range": {"startIndex": metas[i]["text_start"], "endIndex": metas[j]["para_end"]},
                "bulletPreset": preset}})
            i = j + 1
        else:
            i += 1

    # inline text styles
    for meta in metas:
        base = meta["text_start"]
        for sp in meta["spans"]:
            style, fields = {}, []
            if sp.get("bold"):
                style["bold"] = True; fields.append("bold")
            if sp.get("italic"):
                style["italic"] = True; fields.append("italic")
            if sp.get("link"):
                style["link"] = {"url": sp["link"]}; fields.append("link")
            if sp.get("code"):
                style["weightedFontFamily"] = {"fontFamily": "Consolas"}; fields.append("weightedFontFamily")
            if fields:
                requests.append({"updateTextStyle": {
                    "range": {"startIndex": base + sp["start"], "endIndex": base + sp["end"]},
                    "textStyle": style, "fields": ",".join(fields)}})
    return requests
