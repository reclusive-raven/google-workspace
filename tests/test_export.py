"""Export command plumbing (no network; export_file mocked)."""
import types

import gws


def test_default_out_sanitizes():
    assert gws._default_out("My Budget 2026", "pdf") == "My_Budget_2026.pdf"
    assert gws._default_out(None, "csv") == "export.csv"
    assert gws._default_out("a/b:c*d", "xlsx") == "a_b_c_d.xlsx"


def test_export_mime_maps():
    assert gws._DOC_EXPORT["pdf"] == "application/pdf"
    assert gws._DOC_EXPORT["docx"].endswith("wordprocessingml.document")
    assert gws._SHEET_EXPORT["xlsx"].endswith("spreadsheetml.sheet")
    assert gws._SHEET_EXPORT["csv"] == "text/csv"


def test_cmd_doc_export_calls_export_with_right_mime(monkeypatch):
    monkeypatch.setattr(gws, "_resolved",
                        lambda args, **k: (None, "DID", "", None, "doc", {"name": "Report"}))
    captured = {}
    monkeypatch.setattr(gws, "export_file",
                        lambda creds, fid, mime, out, **k: captured.update(fid=fid, mime=mime, out=out))
    args = types.SimpleNamespace(export_as="pdf", out=None, json=True,
                                 target="x", account=None, resource_key=None)
    gws.cmd_doc_export(args)
    assert captured["mime"] == "application/pdf"
    assert captured["out"] == "Report.pdf"           # default name from the doc title


def test_cmd_sheet_export_first_sheet_note(monkeypatch):
    monkeypatch.setattr(gws, "_resolved",
                        lambda args, **k: (None, "SID", "", None, "sheet", {"name": "Data"}))
    monkeypatch.setattr(gws, "export_file", lambda *a, **k: None)
    args = types.SimpleNamespace(export_as="csv", out="out.csv", json=True,
                                 target="x", account=None, resource_key=None)
    assert gws.cmd_sheet_export(args) == 0
