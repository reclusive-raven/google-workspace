"""Write-audit journal (no network)."""
import json

import gws_safety as S


def test_journal_appends_json_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "WRITES_LOG", tmp_path / "writes.log")
    S.journal({"op": "doc.append", "id": "X", "account": "a@b", "detail": {"chars": 3}, "ok": True})
    S.journal({"op": "sheet.clear", "id": "Y"})
    lines = (tmp_path / "writes.log").read_text().strip().split("\n")
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["op"] == "doc.append" and first["id"] == "X" and "ts" in first


def test_journal_never_raises(monkeypatch):
    # a bad path must not blow up a real write
    monkeypatch.setattr(S, "WRITES_LOG", tmp_path_that_cannot_exist())
    S.journal({"op": "x"})  # should silently swallow


def tmp_path_that_cannot_exist():
    from pathlib import Path
    return Path("/dev/null/nope/writes.log")  # parent is a file -> mkdir fails, journal swallows
