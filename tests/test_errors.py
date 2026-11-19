"""HttpError classification -> exit codes (no network)."""
import json

from googleapiclient.errors import HttpError

import gws_http as H


class _Resp(dict):
    def __init__(self, status):
        super().__init__()
        self.status = status
        self.reason = "error"


def _err(status, body):
    return HttpError(_Resp(status), json.dumps(body).encode(), uri="https://api")


def test_docs_style_scope_403_maps_to_auth():
    # newer Docs/Sheets APIs: no errors[].reason array, status PERMISSION_DENIED + scope message
    body = {"error": {"code": 403, "message": "Request had insufficient authentication scopes.",
                      "status": "PERMISSION_DENIED"}}
    g = H.classify_http_error(_err(403, body))
    assert g.exit_code == H.EXIT_AUTH


def test_drive_style_scope_403_maps_to_auth():
    # legacy Drive v3 shape: errors[].reason = insufficientPermissions
    body = {"error": {"code": 403, "message": "Insufficient Permission",
                      "errors": [{"message": "Insufficient Permission", "reason": "insufficientPermissions"}]}}
    g = H.classify_http_error(_err(403, body))
    assert g.exit_code == H.EXIT_AUTH


def test_real_file_permission_403_maps_to_permission():
    body = {"error": {"code": 403, "message": "The caller does not have permission",
                      "status": "PERMISSION_DENIED"}}
    g = H.classify_http_error(_err(403, body))
    assert g.exit_code == H.EXIT_PERMISSION


def test_api_disabled_403_maps_to_permission_with_enable_hint():
    body = {"error": {"code": 403,
                      "message": "Google Docs API has not been used in project 123 before or it is disabled.",
                      "status": "PERMISSION_DENIED"}}
    g = H.classify_http_error(_err(403, body))
    assert g.exit_code == H.EXIT_PERMISSION
    assert "enable" in g.remedy.lower()


def test_not_found_404():
    body = {"error": {"code": 404, "message": "File not found: x.", "status": "NOT_FOUND"}}
    assert H.classify_http_error(_err(404, body)).exit_code == H.EXIT_NOT_FOUND


def test_revision_conflict_400():
    body = {"error": {"code": 400, "message": "requiredRevisionId does not match."}}
    assert H.classify_http_error(_err(400, body)).exit_code == H.EXIT_CONFLICT
