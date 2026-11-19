"""Rendering: Google Docs JSON -> Markdown, and Sheets values -> markdown/csv/tsv/json.

Docs structural notes encoded here:
  - indices/lengths are UTF-16 code units; every paragraph's text ends in a newline.
  - ordered-list item numbers are NOT in the API response - we count per (listId, level).
  - image contentUri expires (~30 min), so alt text is the durable signal.
"""
from __future__ import annotations

import json
import re

# ---------------------------------------------------------------- Docs -> Markdown

_ORDERED_GLYPHS = ("DECIMAL", "ZERO_DECIMAL", "ALPHA", "ROMAN")  # substring match, case-insensitive


def doc_to_markdown(doc: dict, *, include_title: bool = True, plain: bool = False, tab_id=None) -> str:
    out: list[str] = []
    if include_title and doc.get("title"):
        out.append(("# " + doc["title"]) if not plain else doc["title"])
        out.append("")
    tabs = _collect_tabs(doc)
    if tab_id:
        matched = _match_tabs(tabs, tab_id)
        if matched:  # if the id doesn't match any tab, fall back to rendering all
            tabs = matched
    multi = len(tabs) > 1
    for title, body, lists, objects, _tid in tabs:
        if multi and title:
            out.append(("## " + title) if not plain else title)
            out.append("")
        counters: dict = {}
        for el in body.get("content", []):
            if "paragraph" in el:
                _render_paragraph(el["paragraph"], lists, objects, counters, out, plain, include_title)
            elif "table" in el:
                _render_table(el["table"], out, plain)
            # sectionBreak / tableOfContents -> skip
    return _collapse_blanks("\n".join(out))


def _collect_tabs(doc: dict):
    """Return [(title, body, lists, inlineObjects, tabId)] - one per tab when tabs are present, else
    the legacy single body. With includeTabsContent, lists/inlineObjects live PER TAB
    (tab.documentTab.lists), not at the document top level, so they must travel with their body."""
    if doc.get("tabs"):
        result = []
        for tab in _flatten_tabs(doc["tabs"]):
            dt = tab.get("documentTab") or {}
            body = dt.get("body")
            if body is None:
                continue
            props = tab.get("tabProperties") or {}
            result.append((props.get("title"), body, dt.get("lists") or {},
                           dt.get("inlineObjects") or {}, props.get("tabId")))
        if result:
            return result
    return [(None, doc.get("body") or {}, doc.get("lists") or {}, doc.get("inlineObjects") or {}, None)]


def _norm_tab(x):
    x = (x or "").lower()
    return x[2:] if x.startswith("t.") else x  # URLs carry a "t." prefix; tabProperties.tabId may not


def _match_tabs(tabs, tab_id):
    want = _norm_tab(tab_id)
    return [t for t in tabs if _norm_tab(t[4]) == want]


def _flatten_tabs(tabs: list) -> list:
    out = []
    for t in tabs:
        out.append(t)
        for child in t.get("childTabs") or []:
            out.extend(_flatten_tabs([child]))
    return out


def _render_paragraph(p, lists, objects, counters, out, plain, include_title):
    text = "".join(_render_element(e, objects, plain) for e in p.get("elements", [])).rstrip("\n")
    bullet = p.get("bullet")
    if bullet:
        level = bullet.get("nestingLevel", 0)
        list_id = bullet.get("listId")
        indent = "  " * level
        if _is_ordered(_glyph(lists, list_id, level)):
            n = _bump(counters, (list_id, level))
            out.append(f"{indent}{n}. {text}")
        else:
            out.append(f"{indent}- {text}")
        return

    style = (p.get("paragraphStyle") or {}).get("namedStyleType", "NORMAL_TEXT")
    if style == "TITLE":
        # doc title already emitted as H1 when include_title; avoid a duplicate H1
        out.append(("**" + text + "**") if include_title else ("# " + text))
    elif style == "SUBTITLE":
        out.append(("_" + text + "_") if text else "")
    elif style.startswith("HEADING_"):
        counters.clear()  # a heading breaks list numbering
        level = int(style.split("_")[1])
        out.append(("#" * level + " " + text) if not plain else text)
    elif text == "":
        out.append("")
    else:
        out.append(text)


def _render_element(e, objects, plain):
    if "textRun" in e:
        tr = e["textRun"]
        core = (tr.get("content", "") or "").replace("\v", "\n")
        style = tr.get("textStyle", {}) or {}
        if plain:
            return core
        marked = _apply_marks(core, style)
        link = (style.get("link") or {}).get("url")
        return f"[{marked.strip()}]({link})" if (link and marked.strip()) else marked
    if "inlineObjectElement" in e and not plain:
        oid = e["inlineObjectElement"].get("inlineObjectId")
        emb = ((objects.get(oid, {}) or {}).get("inlineObjectProperties", {}) or {}).get("embeddedObject", {})
        alt = emb.get("title") or emb.get("description") or "image"
        uri = (emb.get("imageProperties", {}) or {}).get("contentUri", "")
        return f"![{alt}]({uri})"
    if "footnoteReference" in e and not plain:
        return f"[^{e['footnoteReference'].get('footnoteNumber', '')}]"
    return ""


def _apply_marks(t, s):
    if not t or not t.strip():
        return t
    lead = t[: len(t) - len(t.lstrip())]
    trail = t[len(t.rstrip()):]
    core = t.strip()
    if s.get("bold") and s.get("italic"):
        core = f"***{core}***"
    elif s.get("bold"):
        core = f"**{core}**"
    elif s.get("italic"):
        core = f"*{core}*"
    if s.get("strikethrough"):
        core = f"~~{core}~~"
    off = s.get("baselineOffset")
    if off == "SUPERSCRIPT":
        core = f"<sup>{core}</sup>"
    elif off == "SUBSCRIPT":
        core = f"<sub>{core}</sub>"
    return lead + core + trail


def _glyph(lists, list_id, level):
    try:
        return lists[list_id]["listProperties"]["nestingLevels"][level].get("glyphType", "")
    except (KeyError, IndexError, TypeError):
        return ""


def _is_ordered(glyph):
    g = (glyph or "").upper()
    return any(o in g for o in _ORDERED_GLYPHS)


def _bump(counters, key):
    counters[key] = counters.get(key, 0) + 1
    return counters[key]


def _render_table(table, out, plain):
    rows = []
    for row in table.get("tableRows", []):
        rows.append([_cell_text(c.get("content", []), plain) for c in row.get("tableCells", [])])
    if not rows:
        return
    width = max(len(r) for r in rows)
    for r in rows:
        r.extend([""] * (width - len(r)))
    out.append("| " + " | ".join(rows[0]) + " |")
    out.append("|" + "|".join([" --- "] * width) + "|")
    for r in rows[1:]:
        out.append("| " + " | ".join(r) + " |")
    out.append("")


def _cell_text(content, plain):
    parts = []
    for el in content:
        if "paragraph" in el:
            txt = "".join(_render_element(e, {}, plain) for e in el["paragraph"].get("elements", []))
            if txt.strip():
                parts.append(txt.strip())
    return " ".join(parts).replace("|", "\\|").replace("\n", "<br>")


def _collapse_blanks(md):
    result, blank = [], 0
    for ln in md.split("\n"):
        if ln.strip() == "":
            blank += 1
            if blank <= 1:
                result.append("")
        else:
            blank = 0
            result.append(ln)
    return "\n".join(result).strip() + "\n"


def slice_section(md, heading):
    """Return the slice from a heading whose text matches `heading` to the next same-or-higher
    heading, or None if not found."""
    lines = md.split("\n")
    target = heading.strip().lower()
    start, start_level = None, 0
    for i, ln in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m and m.group(2).strip().lower() == target:
            start, start_level = i, len(m.group(1))
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        m = re.match(r"^(#{1,6})\s+", lines[j])
        if m and len(m.group(1)) <= start_level:
            end = j
            break
    return "\n".join(lines[start:end]).strip() + "\n"


def truncate(md, max_chars):
    if max_chars and len(md) > max_chars:
        return md[:max_chars].rstrip() + (
            f"\n\n> [truncated at {max_chars} chars; narrow with --section or raise --max-chars]\n")
    return md


# ---------------------------------------------------------------- Sheets values -> text

def sheet_values_to_text(values, fmt="markdown", *, max_rows=None, max_cols=None):
    """values: list[list]. Returns (text, truncated)."""
    if not values:
        return "(empty range)", False
    truncated = False
    rows = values
    if max_rows and len(rows) > max_rows:
        rows, truncated = rows[:max_rows], True
    width = max((len(r) for r in rows), default=0)
    if max_cols and width > max_cols:
        width, truncated = max_cols, True
    norm = [[_cellstr(r[c]) if c < len(r) else "" for c in range(width)] for r in rows]

    if fmt == "json":
        text = json.dumps(norm, ensure_ascii=False)
    elif fmt in ("csv", "tsv"):
        sep = "," if fmt == "csv" else "\t"
        text = "\n".join(sep.join(_delimited(c, sep) for c in row) for row in norm)
    else:
        text = _sheet_markdown(norm)
    return text, truncated


def _cellstr(v):
    return "" if v is None else str(v)


def _delimited(v, sep):
    s = _cellstr(v)
    if sep in s or '"' in s or "\n" in s:
        return '"' + s.replace('"', '""') + '"'
    return s


def _sheet_markdown(rows):
    if not rows:
        return "(empty range)"
    width = len(rows[0])

    def fmt_row(r):
        return "| " + " | ".join(_cellstr(c).replace("|", "\\|").replace("\n", "<br>") for c in r) + " |"

    out = [fmt_row(rows[0]), "|" + "|".join([" --- "] * width) + "|"]
    out += [fmt_row(r) for r in rows[1:]]
    return "\n".join(out)
