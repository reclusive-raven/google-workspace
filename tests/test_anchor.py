"""find_index anchor resolution over a fixture doc (no network)."""
import pytest

import gws_docs
from gws_http import GwsError


def _doc():
    # "Intro\n" occupies indices 1..7 (len 6); "hello world\n" occupies 7..19 (len 12).
    return {"tabs": [{
        "tabProperties": {"tabId": "t.a", "title": "T"},
        "documentTab": {"lists": {}, "inlineObjects": {}, "body": {"content": [
            {"startIndex": 1, "endIndex": 7, "paragraph": {
                "paragraphStyle": {"namedStyleType": "HEADING_1"},
                "elements": [{"startIndex": 1, "endIndex": 7, "textRun": {"content": "Intro\n"}}]}},
            {"startIndex": 7, "endIndex": 19, "paragraph": {
                "elements": [{"startIndex": 7, "endIndex": 19, "textRun": {"content": "hello world\n"}}]}},
        ]}}}]}


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    monkeypatch.setattr(gws_docs, "get_document", lambda *a, **k: _doc())


def test_at_start():
    assert gws_docs.find_index(None, "id", at_start=True) == 1


def test_after_heading_returns_paragraph_end():
    assert gws_docs.find_index(None, "id", after_heading="Intro") == 7
    assert gws_docs.find_index(None, "id", after_heading="intro") == 7  # case-insensitive


def test_after_text_returns_index_past_match():
    # "hello" starts at 7, ends 5 code units later -> 12
    assert gws_docs.find_index(None, "id", after_text="hello") == 12


def test_missing_heading_raises():
    with pytest.raises(GwsError):
        gws_docs.find_index(None, "id", after_heading="nope")


def test_missing_text_raises():
    with pytest.raises(GwsError):
        gws_docs.find_index(None, "id", after_text="absent")
