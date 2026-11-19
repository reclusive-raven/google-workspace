#!/usr/bin/env -S uv run --extra dev python
"""Live end-to-end smoke test for the google-workspace skill.

Creates a throwaway Google Doc + Sheet in your Drive (using the scopes already granted), drives the
real gws.py CLI against them through every write / restore / export path, asserts the read-backs, then
prints the scratch URLs. It deletes NOTHING (no trash scope; safer to leave evidence you can inspect).

Run (from the repo root):  uv run --extra dev python tests/live_smoke.py
Needs a completed `auth login` (token.json). Nothing here touches your real finances/medical files
except an optional READ-ONLY sensitivity probe.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
GWS = SCRIPTS / "gws.py"
sys.path.insert(0, str(SCRIPTS))

from gws_auth import account_email, resolve_credentials, docs_service, sheets_service  # noqa: E402

PASS, FAIL = "\033[32m✓\033[0m", "\033[31m✗\033[0m"
_results = []


def check(name, ok, extra=""):
    _results.append(ok)
    print(f"  {PASS if ok else FAIL} {name}" + (f"  ({extra})" if extra and not ok else ""))
    return ok


def run(*args, stdin=None):
    """Invoke gws.py --json; return (exit_code, parsed_json_or_text)."""
    env = {**os.environ, "PYTHONPATH": str(SCRIPTS)}
    proc = subprocess.run([sys.executable, str(GWS), *args, "--json"],
                          input=stdin, capture_output=True, text=True, env=env)
    out = proc.stdout.strip()
    try:
        data = json.loads(out) if out else {}
    except json.JSONDecodeError:
        data = {"_raw": out}
    return proc.returncode, data


def run_text(*args):
    """Invoke gws.py without --json; return (exit_code, stdout text)."""
    env = {**os.environ, "PYTHONPATH": str(SCRIPTS)}
    proc = subprocess.run([sys.executable, str(GWS), *args], capture_output=True, text=True, env=env)
    return proc.returncode, proc.stdout


def main():
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    creds = resolve_credentials("write")
    email = account_email(creds)
    print(f"\nAuthenticated as: {email}\n")

    print("Creating scratch files…")
    doc_id = docs_service(creds).documents().create(
        body={"title": f"gws-smoke doc {ts}"}).execute()["documentId"]
    ss = sheets_service(creds).spreadsheets().create(
        body={"properties": {"title": f"gws-smoke sheet {ts}"}}).execute()
    sheet_id = ss["spreadsheetId"]
    tab = ss["sheets"][0]["properties"]["title"]  # usually "Sheet1"
    doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"
    sheet_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
    print(f"  doc:   {doc_url}\n  sheet: {sheet_url}\n")

    tmp = Path(tempfile.mkdtemp(prefix="gws-smoke-"))

    # --- auth + account guard ------------------------------------------------------------
    print("auth / account:")
    rc, d = run("auth", "status")
    check("auth status returns email", rc == 0 and d.get("email") == email, str(d))
    rc, _ = run("doc", "read", doc_url, "--account", email or "x")
    check("--account matching -> ok", rc == 0)
    rc, _ = run("doc", "read", doc_url, "--account", "nobody@example.com")
    check("--account wrong -> exit 2", rc == 2)

    # --- doc: markdown append, anchor insert, replace, restore ---------------------------
    print("doc writes:")
    md = "# Smoke H1\n\n**bold** and [link](https://x.io)\n\n- one\n- two"
    rc, _ = run("doc", "append", doc_url, "--markdown", "--text", md, "--yes")
    check("append --markdown -> ok", rc == 0)
    rc, body = run_text("doc", "read", doc_url)
    check("markdown heading rendered", "# Smoke H1" in body, body[:200])
    check("markdown bold rendered", "**bold**" in body)
    check("markdown link rendered", "[link](https://x.io)" in body)
    check("markdown bullet rendered", "- one" in body and "- two" in body)

    rc, _ = run("doc", "insert", doc_url, "--after-heading", "Smoke H1", "--text", "INSERTED\n", "--yes")
    check("insert --after-heading -> ok", rc == 0)
    rc, body = run_text("doc", "read", doc_url)
    check("inserted text present", "INSERTED" in body)

    rc, d = run("doc", "replace", doc_url, "--find", "INSERTED", "--replace", "SWAPPED",
                "--yes", "--backup")
    doc_backup = d.get("backup")
    check("replace -> ok + backup written", rc == 0 and bool(doc_backup), str(d))
    rc, body = run_text("doc", "read", doc_url)
    check("replace applied", "SWAPPED" in body and "INSERTED" not in body)
    rc, _ = run("restore", doc_backup, "--yes")
    rc2, body = run_text("doc", "read", doc_url)
    check("restore reversed the replace", rc == 0 and "INSERTED" in body and "SWAPPED" not in body)

    # --- sheet: append, update+restore, clear+restore, batch-update ----------------------
    print("sheet writes:")
    rc, _ = run("sheet", "append", sheet_url, "--range", f"{tab}!A1",
                "--values-json", '[["alpha","beta"]]', "--yes")
    check("sheet append -> ok", rc == 0)
    rc, body = run_text("sheet", "read", sheet_url, "--range", f"{tab}!A1:B1")
    check("appended row reads back", "alpha" in body and "beta" in body, body)

    rc, d = run("sheet", "update", sheet_url, "--range", f"{tab}!A1:B1",
                "--values-json", '[["x","y"]]', "--yes", "--backup")
    upd_backup = d.get("backup")
    check("sheet update -> ok + backup", rc == 0 and bool(upd_backup), str(d))
    rc, body = run_text("sheet", "read", sheet_url, "--range", f"{tab}!A1:B1")
    check("update applied", "x" in body and "y" in body)
    run("restore", upd_backup, "--yes")
    rc, body = run_text("sheet", "read", sheet_url, "--range", f"{tab}!A1:B1")
    check("restore brought values back", "alpha" in body and "beta" in body)

    rc, d = run("sheet", "clear", sheet_url, "--range", f"{tab}!A1:B1", "--yes", "--backup")
    clr_backup = d.get("backup")
    check("sheet clear -> ok + backup", rc == 0 and bool(clr_backup))
    run("restore", clr_backup, "--yes")
    rc, body = run_text("sheet", "read", sheet_url, "--range", f"{tab}!A1:B1")
    check("restore un-cleared the range", "alpha" in body)

    rc, _ = run("sheet", "batch-update", sheet_url, "--yes", "--data-json",
                json.dumps([{"range": f"{tab}!A1", "values": [["p"]]},
                            {"range": f"{tab}!C1", "values": [["q"]]}]))
    check("batch-update two ranges -> ok", rc == 0)
    rc, body = run_text("sheet", "read", sheet_url, "--ranges", f"{tab}!A1", f"{tab}!C1")
    check("batch-update wrote both ranges", "p" in body and "q" in body, body)

    # --- export --------------------------------------------------------------------------
    print("export:")
    for kind, fmt, sniff in [("doc", "pdf", b"%PDF"), ("doc", "docx", b"PK"),
                             ("sheet", "xlsx", b"PK"), ("sheet", "csv", None)]:
        url = doc_url if kind == "doc" else sheet_url
        out = tmp / f"{kind}.{fmt}"
        rc, _ = run(kind, "export", url, "--as", fmt, "--out", str(out))
        ok = rc == 0 and out.exists() and out.stat().st_size > 0
        if ok and sniff:
            ok = out.read_bytes()[:4].startswith(sniff)
        check(f"{kind} export --as {fmt}", ok)

    # --- audit journal + backups exist ---------------------------------------------------
    print("audit trail:")
    from gws_auth import SECRETS_DIR
    wl = SECRETS_DIR / "writes.log"
    check("writes.log has entries", wl.exists() and wl.read_text().strip() != "")
    check("backups dir populated", (SECRETS_DIR / "backups").exists()
          and any((SECRETS_DIR / "backups").iterdir()))

    # --- sensitivity probe (READ-ONLY) ---------------------------------------------------
    print("sensitivity (read-only):")
    rc, d = run("resolve", doc_url)
    check("scratch doc classified normal", rc == 0 and d.get("sensitive") is False, str(d))
    stub = _find_finances_stub()
    if stub:
        rc, d = run("resolve", stub)
        check(f"finances stub classified sensitive ({Path(stub).name})",
              rc == 0 and d.get("sensitive") is True, str(d))
    else:
        print("  – no local finances stub found; skipped live sensitive check")

    # --- summary -------------------------------------------------------------------------
    passed, total = sum(_results), len(_results)
    print(f"\n{'='*54}\n{passed}/{total} checks passed")
    print(f"\nScratch files (delete when done):\n  {doc_url}\n  {sheet_url}")
    print(f"Export artifacts: {tmp}")
    return 0 if passed == total else 1


def _find_finances_stub():
    """Best-effort: a locally-synced .gsheet/.gdoc under a Drive finances/ folder, for the read-only
    sensitivity probe. Returns None if nothing is synced locally."""
    import glob
    roots = glob.glob(str(Path.home() / "Library/CloudStorage/GoogleDrive-*/My Drive/finances"))
    for root in roots:
        for ext in ("*.gsheet", "*.gdoc"):
            hits = glob.glob(os.path.join(root, "**", ext), recursive=True)
            if hits:
                return hits[0]
    return None


if __name__ == "__main__":
    sys.exit(main())
