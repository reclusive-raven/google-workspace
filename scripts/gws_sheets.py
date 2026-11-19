"""Google Sheets read + write operations.

The Sheets values API has no revision token, so writes cannot use optimistic concurrency the way
Docs can. The guard for destructive Sheets writes is the pre-write backup (see gws_safety).
"""
from __future__ import annotations

from gws_auth import sheets_service
from gws_http import execute

_RENDER = {"formatted": "FORMATTED_VALUE", "unformatted": "UNFORMATTED_VALUE", "formula": "FORMULA"}


def render_option(name):
    return _RENDER.get((name or "formatted").lower(), "FORMATTED_VALUE")


def metadata(creds, sheet_id, resource_key=""):
    svc = sheets_service(creds)
    req = svc.spreadsheets().get(
        spreadsheetId=sheet_id,
        fields="properties.title,sheets(properties(sheetId,title,index,"
               "gridProperties(rowCount,columnCount)))")
    return execute(req, file_id=sheet_id, resource_key=resource_key)


def read_range(creds, sheet_id, rng, *, render="FORMATTED_VALUE", resource_key=""):
    svc = sheets_service(creds)
    req = svc.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=rng, valueRenderOption=render,
        dateTimeRenderOption="FORMATTED_STRING", majorDimension="ROWS")
    return execute(req, file_id=sheet_id, resource_key=resource_key).get("values", [])


def batch_get(creds, sheet_id, ranges, *, render="FORMATTED_VALUE", resource_key=""):
    svc = sheets_service(creds)
    req = svc.spreadsheets().values().batchGet(
        spreadsheetId=sheet_id, ranges=ranges, valueRenderOption=render,
        dateTimeRenderOption="FORMATTED_STRING")
    return execute(req, file_id=sheet_id, resource_key=resource_key).get("valueRanges", [])


def update(creds, sheet_id, rng, values, *, value_input="USER_ENTERED", resource_key=""):
    svc = sheets_service(creds)
    req = svc.spreadsheets().values().update(
        spreadsheetId=sheet_id, range=rng, valueInputOption=value_input, body={"values": values})
    return execute(req, file_id=sheet_id, resource_key=resource_key)


def append(creds, sheet_id, rng, values, *, value_input="USER_ENTERED", resource_key=""):
    svc = sheets_service(creds)
    req = svc.spreadsheets().values().append(
        spreadsheetId=sheet_id, range=rng, valueInputOption=value_input,
        insertDataOption="INSERT_ROWS", body={"values": values})
    return execute(req, file_id=sheet_id, resource_key=resource_key)


def clear(creds, sheet_id, rng, *, resource_key=""):
    svc = sheets_service(creds)
    req = svc.spreadsheets().values().clear(spreadsheetId=sheet_id, range=rng, body={})
    return execute(req, file_id=sheet_id, resource_key=resource_key)


def batch_update_values(creds, sheet_id, data, *, value_input="USER_ENTERED", resource_key=""):
    """Atomic multi-range write. data = [{"range": "S!A1", "values": [[...]]}, ...].

    Unlike calling update() per range, this either applies every range or none - one API round-trip.
    """
    svc = sheets_service(creds)
    body = {"valueInputOption": value_input,
            "data": [{"range": d["range"], "values": d["values"]} for d in data]}
    req = svc.spreadsheets().values().batchUpdate(spreadsheetId=sheet_id, body=body)
    return execute(req, file_id=sheet_id, resource_key=resource_key)
