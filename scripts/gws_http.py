"""Shared HTTP concerns for every Google API call in this skill.

execute() is the single choke point through which all Docs/Sheets/Drive requests go. It:
  - injects the X-Goog-Drive-Resource-Keys header for resource-keyed (link-shared) files,
  - retries 429/5xx with exponential backoff (honoring Retry-After),
  - raises a GwsError carrying an exit code + actionable remedy on terminal failures.

Exit codes are the skill's contract with its caller (Claude); they stay in sync with SKILL.md
and references/errors.md.
"""
from __future__ import annotations

import json
import random
import time

from googleapiclient.errors import HttpError

EXIT_OK = 0
EXIT_GENERIC = 1
EXIT_AUTH = 2
EXIT_NEEDS_CONFIRM = 3
EXIT_NOT_FOUND = 4
EXIT_PERMISSION = 5
EXIT_RATE_LIMITED = 6
EXIT_CONFLICT = 7

_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 5


class GwsError(Exception):
    """A classified, user-facing failure. Carries an exit code and a remedy string."""

    def __init__(self, message, *, exit_code=EXIT_GENERIC, remedy="", code=None):
        super().__init__(message)
        self.message = str(message)
        self.exit_code = exit_code
        self.remedy = remedy
        self.code = code  # HTTP status when applicable


def execute(request, *, file_id=None, resource_key=None):
    """Run a googleapiclient request with the resource-key header + retry/backoff.

    On a terminal HttpError, raises a classified GwsError.
    """
    if resource_key and file_id:
        request.headers["X-Goog-Drive-Resource-Keys"] = f"{file_id}/{resource_key}"
    attempt = 0
    while True:
        try:
            return request.execute()
        except HttpError as exc:
            status = _status(exc)
            if status in _RETRY_STATUS and attempt < _MAX_ATTEMPTS - 1:
                time.sleep(_retry_delay(exc, attempt))
                attempt += 1
                continue
            raise classify_http_error(exc) from exc


def _status(exc):
    status = getattr(exc.resp, "status", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _retry_delay(exc, attempt):
    # honor a Retry-After header when present, else exponential backoff with jitter
    try:
        retry_after = exc.resp.get("retry-after")
        if retry_after:
            return float(retry_after)
    except (AttributeError, ValueError, TypeError):
        pass
    return min(32.0, float(2 ** attempt)) + random.uniform(0, 1)


def _parse_reason(exc):
    """Return (reason, grpc_status, message) parsed defensively from the error body."""
    try:
        raw = exc.content.decode() if isinstance(exc.content, bytes) else exc.content
        err = json.loads(raw).get("error", {})
        if isinstance(err, dict):
            errors = err.get("errors") or []
            reason = errors[0].get("reason") if errors else None
            return reason, err.get("status"), err.get("message", "")
    except Exception:
        pass
    return None, None, str(exc)


def classify_http_error(exc):
    status = _status(exc)
    reason, grpc_status, message = _parse_reason(exc)
    r = (reason or "").lower()
    msg = message or str(exc)
    msg_l = msg.lower()

    # A missing/insufficient SCOPE shows up as 401 (legacy) or 403 (newer Docs/Sheets APIs, which
    # omit the errors[].reason array and only say so in the message). Catch both shapes -> re-auth.
    scope_problem = ("insufficient authentication scope" in msg_l
                     or "insufficient" in r
                     or ("scope" in msg_l and "insufficient" in msg_l))
    if status in (401, 403) and scope_problem:
        return GwsError(msg, exit_code=EXIT_AUTH, code=status,
                        remedy="Token lacks the needed scope, or setup isn't finished. Run: "
                               "gws.py auth login --scopes write (and enable the Docs/Sheets/Drive APIs).")
    if status == 401 or grpc_status == "UNAUTHENTICATED":
        return GwsError(msg, exit_code=EXIT_AUTH, code=status,
                        remedy="Re-authenticate: gws.py auth login. If it keeps expiring, set the "
                               "OAuth consent screen to In production.")
    if status == 403:
        if "rate" in r:
            return GwsError(msg, exit_code=EXIT_RATE_LIMITED, code=status,
                            remedy="Rate-limited; try again shortly.")
        if ("accessnotconfigured" in r or "servicedisabled" in r
                or "has not been used" in msg_l or "api has not been used" in msg_l):
            return GwsError(msg, exit_code=EXIT_PERMISSION, code=status,
                            remedy="Enable the Google Docs/Sheets/Drive API in the Cloud project, then retry.")
        return GwsError(msg, exit_code=EXIT_PERMISSION, code=status,
                        remedy="You have view-only or no access to this file; ask the owner for edit rights.")
    if status == 404:
        return GwsError(msg, exit_code=EXIT_NOT_FOUND, code=status,
                        remedy="No document/sheet with that id for this account; check the id, confirm it's "
                               "shared with you, or pass --resource-key for a link-shared file.")
    if status == 400 and ("revision" in msg.lower() or "requiredrevisionid" in msg.lower()):
        return GwsError(msg, exit_code=EXIT_CONFLICT, code=status,
                        remedy="Document changed since it was read; re-run to apply on the latest revision.")
    if status == 429:
        return GwsError(msg, exit_code=EXIT_RATE_LIMITED, code=status,
                        remedy="Rate-limited; try again shortly.")
    return GwsError(msg, exit_code=EXIT_GENERIC, code=status)
