"""restore command dispatch + gating (no network; API calls mocked)."""
import types

import gws
import gws_safety
from gws_http import EXIT_OK, EXIT_NEEDS_CONFIRM


def _args(backup_file, **k):
    d = {"backup_file": backup_file, "dry_run": False, "yes": False, "json": True,
         "account": None, "resource_key": None}
    d.update(k)
    return types.SimpleNamespace(**d)


def _patch_common(tmp_path, monkeypatch):
    monkeypatch.setattr(gws_safety, "BACKUP_DIR", tmp_path)
    monkeypatch.setattr(gws_safety, "WRITES_LOG", tmp_path / "writes.log")
    monkeypatch.setattr(gws, "resolve_credentials", lambda *a, **k: None)
    monkeypatch.setattr(gws, "account_email", lambda creds=None: None)  # skip identity assert


def test_restore_sheet_update_uses_user_entered(tmp_path, monkeypatch):
    _patch_common(tmp_path, monkeypatch)
    calls = {}
    monkeypatch.setattr(gws.gws_sheets, "update",
                        lambda creds, sid, rng, vals, **k: calls.update(
                            sid=sid, rng=rng, vals=vals, vin=k.get("value_input")) or {"updatedCells": 2})
    path = gws_safety.write_backup("SID", "sheet-update",
                                   {"range": "S!A1:B1", "before_values": [["a", "b"]]})
    rc = gws.cmd_restore(_args(path, yes=True))
    assert rc == EXIT_OK
    assert calls["vin"] == "USER_ENTERED"          # not RAW - preserves formulas/dates
    assert calls["vals"] == [["a", "b"]] and calls["rng"] == "S!A1:B1"


def test_restore_is_gated_without_yes(tmp_path, monkeypatch):
    _patch_common(tmp_path, monkeypatch)
    touched = []
    monkeypatch.setattr(gws.gws_sheets, "update", lambda *a, **k: touched.append(1))
    path = gws_safety.write_backup("SID", "sheet-update", {"range": "S!A1", "before_values": [["x"]]})
    rc = gws.cmd_restore(_args(path))  # no --yes
    assert rc == EXIT_NEEDS_CONFIRM and not touched


def test_restore_doc_replace_reverses(tmp_path, monkeypatch):
    _patch_common(tmp_path, monkeypatch)
    seen = {}
    monkeypatch.setattr(gws.gws_docs, "replace_all",
                        lambda creds, did, find, repl, **k: seen.update(find=find, repl=repl) or ({}, 2))
    path = gws_safety.write_backup("DID", "doc-replace",
                                   {"find": "old", "replace": "new", "match_case": True})
    rc = gws.cmd_restore(_args(path, yes=True))
    assert rc == EXIT_OK
    assert seen["find"] == "new" and seen["repl"] == "old"  # reversed direction


def test_restore_empty_before_clears(tmp_path, monkeypatch):
    _patch_common(tmp_path, monkeypatch)
    cleared = []
    monkeypatch.setattr(gws.gws_sheets, "clear", lambda creds, sid, rng, **k: cleared.append(rng) or {})
    monkeypatch.setattr(gws.gws_sheets, "update", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should clear")))
    path = gws_safety.write_backup("SID", "sheet-update", {"range": "S!A1:B2", "before_values": []})
    rc = gws.cmd_restore(_args(path, yes=True))
    assert rc == EXIT_OK and cleared == ["S!A1:B2"]
