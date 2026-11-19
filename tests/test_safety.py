"""Sensitivity classification + backups (no network)."""
import json

import gws_safety as S


def test_sensitive_folder_segment_match():
    assert S.is_sensitive("/Users/me/.../My Drive/finances/2026/tracker.gsheet")
    assert S.is_sensitive("/x/vault/secret.gdoc")
    assert S.is_sensitive("work/plan.gsheet")


def test_non_sensitive_and_substring_not_flagged():
    # "workflows" contains "work" as a substring but is a different folder name -> NOT sensitive
    assert not S.is_sensitive("/x/workflows/notes.gdoc")
    assert not S.is_sensitive("/x/trips/munich/budget.gsheet")
    assert not S.is_sensitive(None)
    assert not S.is_sensitive("")


def test_classify_returns_labels():
    assert S.classify_sensitivity("finances/x") == "sensitive"
    assert S.classify_sensitivity("trips/x") == "normal"


def test_write_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "BACKUP_DIR", tmp_path / "backups")
    path = S.write_backup("DOCID", "sheet-update", {"range": "A1:B2", "before_values": [["x"]]})
    data = json.loads((tmp_path / "backups" / __import__("os").path.basename(path)).read_text())
    assert data["id"] == "DOCID"
    assert data["op"] == "sheet-update"
    assert data["before_values"] == [["x"]]
    assert "ts" in data
