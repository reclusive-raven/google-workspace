"""Docs-JSON -> Markdown and Sheets-values -> text rendering."""
import gws_render as R


def _run(text, style=None):
    return {"textRun": {"content": text, "textStyle": style or {}}}


def _para(elements, style=None, bullet=None):
    p = {"elements": elements}
    if style:
        p["paragraphStyle"] = {"namedStyleType": style}
    if bullet:
        p["bullet"] = bullet
    return {"paragraph": p}


def test_headings_and_title():
    doc = {"title": "My Doc", "body": {"content": [
        _para([_run("Intro.\n")]),
        _para([_run("Section\n")], style="HEADING_2"),
    ]}}
    md = R.doc_to_markdown(doc)
    assert md.startswith("# My Doc")
    assert "## Section" in md
    assert "Intro." in md


def test_inline_marks_and_links():
    doc = {"body": {"content": [
        _para([_run("bold", {"bold": True}), _run(" and "), _run("link", {"link": {"url": "http://x.io"}})]),
    ]}}
    md = R.doc_to_markdown(doc, include_title=False)
    assert "**bold**" in md
    assert "[link](http://x.io)" in md


def test_ordered_and_unordered_lists():
    lists = {
        "OL": {"listProperties": {"nestingLevels": [{"glyphType": "DECIMAL"}, {"glyphType": "ALPHA"}]}},
        "UL": {"listProperties": {"nestingLevels": [{"glyphType": "DISC"}]}},
    }
    doc = {"lists": lists, "body": {"content": [
        _para([_run("first\n")], bullet={"listId": "OL", "nestingLevel": 0}),
        _para([_run("second\n")], bullet={"listId": "OL", "nestingLevel": 0}),
        _para([_run("nested\n")], bullet={"listId": "OL", "nestingLevel": 1}),
        _para([_run("dot\n")], bullet={"listId": "UL", "nestingLevel": 0}),
    ]}}
    md = R.doc_to_markdown(doc, include_title=False)
    assert "1. first" in md
    assert "2. second" in md
    assert "  1. nested" in md  # nested ordered restarts at its own level
    assert "- dot" in md


def test_table_renders_pipe_table():
    doc = {"body": {"content": [
        {"table": {"tableRows": [
            {"tableCells": [{"content": [_para([_run("H1")])]}, {"content": [_para([_run("H2")])]}]},
            {"tableCells": [{"content": [_para([_run("a")])]}, {"content": [_para([_run("b")])]}]},
        ]}},
    ]}}
    md = R.doc_to_markdown(doc, include_title=False)
    assert "| H1 | H2 |" in md
    assert "| a | b |" in md
    assert "| --- |" in md.replace(" ", " ") or "---" in md


def test_plain_mode_drops_markup():
    doc = {"body": {"content": [_para([_run("x", {"bold": True})], style="HEADING_1")]}}
    md = R.doc_to_markdown(doc, include_title=False, plain=True)
    assert "**" not in md
    assert "#" not in md
    assert "x" in md


def test_slice_section():
    md = "# Title\n\n## A\nalpha\n\n## B\nbeta\n"
    out = R.slice_section(md, "A")
    assert "alpha" in out and "beta" not in out
    assert R.slice_section(md, "missing") is None


def test_truncate():
    assert R.truncate("abcdef", 3).startswith("abc")
    assert "truncated" in R.truncate("abcdef", 3)
    assert R.truncate("abc", 100) == "abc"


def test_sheet_markdown_and_csv():
    values = [["Name", "Score"], ["Ann", "9"], ["Bo,b", "7"]]
    md, trunc = R.sheet_values_to_text(values, "markdown")
    assert "| Name | Score |" in md and not trunc
    csv, _ = R.sheet_values_to_text(values, "csv")
    assert '"Bo,b"' in csv  # comma triggers quoting
    tsv, _ = R.sheet_values_to_text(values, "tsv")
    assert "Name\tScore" in tsv


def test_tabs_per_tab_rendering_and_targeting():
    def tab(tid, title, text):
        return {"tabProperties": {"tabId": tid, "title": title},
                "documentTab": {"body": {"content": [_para([_run(text + "\n")])]},
                                "lists": {}, "inlineObjects": {}}}
    doc = {"title": "Multi", "tabs": [tab("t.aaa", "Alpha", "alpha body"),
                                      tab("t.bbb", "Beta", "beta body")]}
    full = R.doc_to_markdown(doc)
    assert "## Alpha" in full and "alpha body" in full
    assert "## Beta" in full and "beta body" in full
    only = R.doc_to_markdown(doc, tab_id="t.bbb")
    assert "beta body" in only and "alpha body" not in only
    only2 = R.doc_to_markdown(doc, tab_id="aaa")  # normalized (t.-stripped) match
    assert "alpha body" in only2 and "beta body" not in only2
    missing = R.doc_to_markdown(doc, tab_id="t.zzz")  # unknown id -> render all
    assert "alpha body" in missing and "beta body" in missing


def test_sheet_truncation_and_empty():
    _, trunc = R.sheet_values_to_text([["a"], ["b"], ["c"]], "csv", max_rows=2)
    assert trunc
    text, _ = R.sheet_values_to_text([], "markdown")
    assert text == "(empty range)"
