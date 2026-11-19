"""Google auth for the google-workspace skill.

Credential resolution order:
  1. cached refreshable secrets/token.json (+ token.backup.json self-heal; refresh-if-expired)
  2. Desktop OAuth client secrets/credentials.json via InstalledAppFlow (interactive; `auth login` only)
  3. gcloud Application Default Credentials, IF they already carry the needed scopes
  else -> AuthError (re-auth needed).

The default `write` scope set edits *referenced* docs/sheets but uses drive.readonly for resolution,
so the token cannot create/move/trash/share files - it can't perform file-level operations on the
sensitive Drive folders. A dedicated token here never clobbers the global ADC.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

logger = logging.getLogger("gws.auth")

SKILL_ROOT = Path(__file__).resolve().parent.parent          # .../google-workspace
SECRETS_DIR = SKILL_ROOT / "secrets"
TOKEN_FILE = SECRETS_DIR / "token.json"
BACKUP_FILE = SECRETS_DIR / "token.backup.json"
CREDENTIALS_FILE = SECRETS_DIR / "credentials.json"
ACCOUNT_FILE = SECRETS_DIR / "account.json"  # cached authenticated email (avoids a network hit per call)

SCOPES = {
    "write": [
        "https://www.googleapis.com/auth/documents",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.readonly",
    ],
    "read": [
        "https://www.googleapis.com/auth/documents.readonly",
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ],
}


class AuthError(RuntimeError):
    """Raised when no usable credential is available (re-auth needed)."""


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)  # atomic on POSIX; never leaves a half-written/empty file


def _persist(creds: Credentials) -> None:
    payload = creds.to_json()
    _atomic_write(TOKEN_FILE, payload)
    _atomic_write(BACKUP_FILE, payload)  # survives a lost token.json


def _load_cached(scopes) -> Credentials | None:
    if not TOKEN_FILE.exists() and BACKUP_FILE.exists():
        logger.warning("token.json missing; restoring from token.backup.json")
        _atomic_write(TOKEN_FILE, BACKUP_FILE.read_text())
    if not TOKEN_FILE.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), scopes)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _persist(creds)
            logger.info("refreshed access token")
            return creds
        except Exception as exc:  # surface as re-auth needed
            logger.warning("token refresh failed: %s", exc)
            return None
    return None


def _try_adc(scopes) -> Credentials | None:
    """Use gcloud ADC only if it already carries the needed scopes."""
    try:
        import google.auth

        creds, _ = google.auth.default(scopes=scopes)
        creds.refresh(Request())
        return creds
    except Exception as exc:
        logger.debug("ADC fallback unavailable: %s", exc)
        return None


def _resolve(scope_mode: str = "write", *, allow_interactive: bool = False):
    """Return (creds, source) or raise AuthError."""
    scopes = SCOPES[scope_mode]

    creds = _load_cached(scopes)
    if creds:
        return creds, "token.json"

    if CREDENTIALS_FILE.exists() and allow_interactive:
        logger.info("starting interactive OAuth consent (one-time)")
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), scopes)
        creds = flow.run_local_server(port=0)
        _persist(creds)
        logger.info("cached token at %s", TOKEN_FILE)
        return creds, "token.json (new)"

    adc = _try_adc(scopes)
    if adc:
        return adc, "ADC"

    raise AuthError(
        "No usable Google credential with the needed scopes. Run `gws.py auth login` once "
        f"(needs {CREDENTIALS_FILE}), or your token lacks the required scope - re-run "
        "`gws.py auth login --scopes write`."
    )


def resolve_credentials(scope_mode: str = "write", *, allow_interactive: bool = False) -> Credentials:
    creds, _ = _resolve(scope_mode, allow_interactive=allow_interactive)
    return creds


def docs_service(creds):
    return build("docs", "v1", credentials=creds, cache_discovery=False)


def sheets_service(creds):
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def drive_service(creds):
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def record_account(email) -> None:
    """Cache the authenticated email so writes can assert identity without a network call."""
    if email:
        _atomic_write(ACCOUNT_FILE, json.dumps({"email": email}))


def cached_account_email():
    """The last-recorded authenticated email, or None. Pure file read - no network."""
    try:
        return json.loads(ACCOUNT_FILE.read_text()).get("email")
    except Exception:
        return None


def account_email(creds=None):
    """Authenticated email: cached if known, else fetched live (and cached) when creds are given."""
    email = cached_account_email()
    if email:
        return email
    if creds is not None:
        try:
            about = drive_service(creds).about().get(fields="user(emailAddress)").execute()
            email = (about.get("user") or {}).get("emailAddress")
            record_account(email)
            return email
        except Exception as exc:
            logger.debug("live account lookup failed: %s", exc)
    return None


def auth_status(scope_mode: str = "write") -> dict:
    """Describe the active credential: source, scopes, expiry, authenticated email."""
    creds, source = _resolve(scope_mode, allow_interactive=False)
    info = {
        "source": source,
        "scopes": sorted(getattr(creds, "scopes", None) or SCOPES[scope_mode]),
        "expiry": creds.expiry.isoformat() + "Z" if getattr(creds, "expiry", None) else None,
        "valid": bool(getattr(creds, "valid", False)),
    }
    # authenticated email via Drive about.get (uses the drive.readonly scope we already hold)
    try:
        about = drive_service(creds).about().get(fields="user(emailAddress,displayName)").execute()
        user = about.get("user", {})
        info["email"] = user.get("emailAddress")
        info["display_name"] = user.get("displayName")
        record_account(info["email"])  # refresh the cache on any status/login
    except Exception as exc:
        info["email"] = None
        info["email_error"] = str(exc)
    return info
