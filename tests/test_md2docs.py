"""Markdown subset -> Docs batchUpdate requests (pure, no network)."""
import gws_md2docs as M


def test_parse_inline_bold_italic_link_code():
    text, spans = M.parse_inline("a **b** _c_ [d](http://x) `e`")
    assert text == "a b c d e"
    by_range = {(s["start"], s["end"]): s for s in spans}
    assert by_range[(2, 3)].get("bold")
    assert by_range[(4, 5)].get("italic")
    assert by_range[(6, 7)].get("link") == "http://x"
    assert by_range[(8, 9)].get("code")


def test_bold_italic_combined():
    text, spans = M.parse_inline("***x***")
    assert text == "x"
    assert spans[0]["bold"] and spans[0]["italic"]


def test_heading_becomes_named_style():
    reqs = M.markdown_to_requests("# Title", start_index=1)
    assert reqs[0]["insertText"] == {"text": "Title\n", "location": {"index": 1}}
    ups = [r["updateParagraphStyle"] for r in reqs if "updateParagraphStyle" in r]
    assert ups[0]["paragraphStyle"]["namedStyleType"] == "HEADING_1"
    assert ups[0]["range"] == {"startIndex": 1, "endIndex": 1 + len("Title\n")}


def test_ordered_list_grouped_into_one_bullet_range():
    reqs = M.markdown_to_requests("1. a\n2. b", start_index=1)
    bullets = [r["createParagraphBullets"] for r in reqs if "createParagraphBullets" in r]
    assert len(bullets) == 1  # consecutive items share one list -> continuous numbering
    assert bullets[0]["bulletPreset"] == "NUMBERED_DECIMAL_ALPHA_ROMAN"
    assert bullets[0]["range"] == {"startIndex": 1, "endIndex": 1 + len("a\nb\n")}


def test_unordered_list_disc():
    reqs = M.markdown_to_requests("- one\n- two", start_index=1)
    bullets = [r["createParagraphBullets"] for r in reqs if "createParagraphBullets" in r]
    assert bullets[0]["bulletPreset"] == "BULLET_DISC_CIRCLE_SQUARE"


def test_inline_ranges_are_absolute():
    reqs = M.markdown_to_requests("**x** [y](u)", start_index=10)
    styles = [r["updateTextStyle"] for r in reqs if "updateTextStyle" in r]
    bold = [s for s in styles if s["textStyle"].get("bold")][0]
    link = [s for s in styles if "link" in s["textStyle"]][0]
    assert bold["range"] == {"startIndex": 10, "endIndex": 11}          # 'x'
    assert link["range"] == {"startIndex": 12, "endIndex": 13}          # 'y'
    assert link["textStyle"]["link"] == {"url": "u"}


def test_star_paragraph_not_a_bullet():
    # a line beginning with *emphasis* (no space) is a paragraph, not a list item
    reqs = M.markdown_to_requests("*just italics*", start_index=1)
    assert not any("createParagraphBullets" in r for r in reqs)
    assert reqs[0]["insertText"]["text"] == "just italics\n"
