"""Write safety: sensitivity classification + recoverable pre-write backups.

Dry-run previews are composed in gws.py (which has the per-command before/after state); this module
owns the two reusable safety primitives: is-this-target-sensitive, and snapshot-before-write.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from gws_auth import SECRETS_DIR

BACKUP_DIR = SECRETS_DIR / "backups"
WRITES_LOG = SECRETS_DIR / "writes.log"  # append-only audit journal of every mutation

# --- Sensitivity policy -------------------------------------------------------------------
# Default sensitive folder names: which folders warrant extra caution on WRITE. A "sensitive"
# verdict forces a backup AND makes even additive writes require --yes, so nothing under these folders is ever mutated silently.
#
# >>> This is a judgment call about YOUR folders - tune the set / the matching rule to taste. <<<
SENSITIVE_FOLDERS = frozenset({
    "vault", "finances", "medical", "work", "people", "academics", "_todo", "archive",
})


def classify_sensitivity(path_or_name) -> str:
    """Return 'sensitive' if the target lives under a sensitive folder, else 'normal'.

    Matches whole path segments (a folder literally named "finances"), not substrings, so a file
    called "workflows.gdoc" is not falsely flagged by the "work" entry.
    """
    if not path_or_name:
        return "normal"
    segments = re.split(r"[\\/]+", str(path_or_name).lower())
    return "sensitive" if any(seg in SENSITIVE_FOLDERS for seg in segments) else "normal"


def is_sensitive(path_or_name) -> bool:
    return classify_sensitivity(path_or_name) == "sensitive"


# --- Backups ------------------------------------------------------------------------------
def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_backup(file_id: str, op: str, payload: dict) -> str:
    """Snapshot pre-write state to secrets/backups/<id>-<op>-<UTC>.json; return the path."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = _timestamp()
    path = BACKUP_DIR / f"{file_id}-{op}-{ts}.json"
    record = {"id": file_id, "op": op, "ts": ts, **payload}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    tmp.replace(path)
    return str(path)


def load_backup(path) -> dict:
    """Read a backup snapshot written by write_backup(); raise GwsError if it isn't one."""
    from gws_http import GwsError, EXIT_NOT_FOUND
    p = Path(path)
    if not p.exists():
        raise GwsError(f"backup not found: {path}", exit_code=EXIT_NOT_FOUND,
                       remedy="Pass a path under secrets/backups/ printed by an earlier write.")
    try:
        data = json.loads(p.read_text())
    except Exception as exc:
        raise GwsError(f"unreadable backup: {path} ({exc})",
                       remedy="Expected the JSON snapshot written by a prior write.")
    if not (data.get("id") and data.get("op")):
        raise GwsError(f"not a gws backup file: {path}",
                       remedy="Pass a path under secrets/backups/ printed by an earlier write.")
    return data


# --- Audit journal ------------------------------------------------------------------------
def journal(entry: dict) -> None:
    """Append one JSON line describing a mutation to secrets/writes.log (best-effort, never raises)."""
    try:
        WRITES_LOG.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"ts": _timestamp(), **entry}, ensure_ascii=False)
        with WRITES_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass  # journaling must never block or fail a real write
