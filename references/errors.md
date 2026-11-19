# Errors, exit codes, and retry

Every API call goes through `gws_http.execute`, which retries transient failures and raises a
classified `GwsError` (exit code + remedy) on terminal ones. `--json` returns the same as
`{"ok": false, "error": {code, message, remedy, exit}}`.

## Exit codes (the caller contract)

| code | meaning | what to do |
|---|---|---|
| 0 | ok | — |
| 1 | generic error (bad target, bad range, malformed values) | fix the argument shown in the message |
| 2 | auth / re-auth needed, **or `--account` ≠ the authenticated email** | `gws.py auth login` (or `--scopes write`); for a mismatch, auth as the requested account or drop `--account` |
| 3 | destructive write needs confirmation, **or a write whose stub owner ≠ you** | a preview was printed; re-run with `--yes` (nothing was written) |
| 4 | not found | check the id; confirm it's shared with you; try `--resource-key` |
| 5 | permission denied / API disabled | ask for edit access, or enable the API in the Cloud project |
| 6 | rate-limited (after retries) | wait and retry |
| 7 | doc changed since read (revision conflict) | just re-run — it re-reads the latest revision |

## Retry / backoff

Retries on `429, 500, 502, 503, 504`: up to 5 attempts, `min(32, 2^attempt) + jitter` seconds,
honoring a `Retry-After` header. All other statuses raise immediately.

## Status → remedy map

| HTTP | reason | mapped exit | remedy |
|---|---|---|---|
| 401 | invalid/expired credentials | 2 | `auth login`; if it keeps expiring, publish the consent screen *In production* |
| 401/403 | `insufficient*` scopes | 2 | `auth login --scopes write` (a read-only token can't write) |
| 403 | `PERMISSION_DENIED` on the file | 5 | you have view-only/no access; ask the owner |
| 403 | `SERVICE_DISABLED` / "has not been used" | 5 | enable Docs/Sheets/Drive API in the Cloud project |
| 403 | `rateLimitExceeded` | 6 | wait and retry |
| 404 | `notFound` | 4 | bad id, trashed, or not shared with this account; try `--resource-key` |
| 400 | revision mismatch | 7 | doc changed since read; re-run |
| 400 | bad range / bad request | 1 | fix the A1 range or request shown in the message |

## Common gotchas

- A **read-only token** doing a write → 401/403 insufficient scopes → exit 2. Mint a write token.
- A file in a **Shared drive** you're not a member of → 403 → exit 5.
- A **link-shared** file without its resource key → 404 → pass `--resource-key` (or use the full URL,
  which carries `?resourcekey=`).
