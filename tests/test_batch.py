"""Atomic multi-range sheet writes: data parsing + request body (no network)."""
import types

import pytest

import gws
import gws_sheets
from gws_http import GwsError


def test_parse_batch_data_valid():
    args = types.SimpleNamespace(data_json='[{"range":"S!A1","values":[["x"]]}]')
    data = gws._parse_batch_data(args)
    assert data[0]["range"] == "S!A1" and data[0]["values"] == [["x"]]


@pytest.mark.parametrize("bad", ["{}", "[]", '[{"range":"a"}]', '[{"values":[[1]]}]', "not json"])
def test_parse_batch_data_invalid(bad):
    with pytest.raises(GwsError):
        gws._parse_batch_data(types.SimpleNamespace(data_json=bad))


def test_batch_update_values_body_shape(monkeypatch):
    captured = {}

    class _Req:
        headers = {}
        def execute(self):
            return {"totalUpdatedCells": 3}

    class _Vals:
        def batchUpdate(self, spreadsheetId, body):
            captured.update(id=spreadsheetId, body=body)
            return _Req()

    class _SS:
        def values(self):
            return _Vals()

    class _Svc:
        def spreadsheets(self):
            return _SS()

    monkeypatch.setattr(gws_sheets, "sheets_service", lambda creds: _Svc())
    data = [{"range": "S!A1", "values": [["a"]]}, {"range": "S!B1", "values": [["b"]]}]
    out = gws_sheets.batch_update_values(None, "SID", data, value_input="RAW")
    assert out["totalUpdatedCells"] == 3
    assert captured["id"] == "SID"
    assert captured["body"]["valueInputOption"] == "RAW"
    assert captured["body"]["data"] == data
