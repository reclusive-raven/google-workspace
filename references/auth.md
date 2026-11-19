# Auth — setup, scopes, troubleshooting

The skill authenticates with a dedicated, isolated token. It never touches your global gcloud ADC.

## Credential resolution order (gws_auth.py)

1. `secrets/token.json` (cached, refreshable; self-heals from `token.backup.json`; auto-refreshes).
2. `secrets/credentials.json` (Desktop OAuth client) via an interactive browser flow — **only** on
   `auth login`.
3. gcloud ADC — used *only if it already carries* the Docs/Sheets/Drive scopes (normally it does
   not, so this is just a fallback that will 403 → prompting `auth login`).

## One-time setup

You need a Desktop OAuth client in a Google Cloud project of your own. Publish its OAuth consent
screen *In production* (not *Testing*), or the refresh token expires after 7 days.

1. **Enable APIs** in that GCP project (Cloud Console → APIs & Services → Enable):
   **Google Docs API**, **Google Sheets API**, **Google Drive API**.
2. **Provide the client:**
   create a Desktop app OAuth client (APIs & Services → Credentials → Create credentials → OAuth
   client ID), download its JSON and save it as `secrets/credentials.json` in the skill folder
   (`~/.claude/skills/google-workspace/secrets/credentials.json`).
   `secrets/credentials.example.json` shows the expected shape.
3. **Consent once:**
   ```
   uv run --script ~/.claude/skills/google-workspace/scripts/gws.py auth login
   ```
   A browser opens; approve. A `token.json` (+ backup) is written to `secrets/`.
4. **Verify:**
   ```
   uv run --script ~/.claude/skills/google-workspace/scripts/gws.py auth status
   ```
   Should show `source: token.json`, your email, and the three scopes.

## Scopes

`auth login` (default `--scopes write`) grants:

- `https://www.googleapis.com/auth/documents` — read + write Docs
- `https://www.googleapis.com/auth/spreadsheets` — read + write Sheets
- `https://www.googleapis.com/auth/drive.readonly` — resolve stubs/URLs to files (read metadata)

This set edits *referenced* docs but **cannot create, move, rename, trash, or share** files — so the
token can't do file-level damage to sensitive folders. `--scopes read` grants the three read-only
equivalents (an extra-safe, write-incapable token).

`drive.file` is deliberately **not** used: it only sees files the app created or the user picked, so
it can't resolve your existing stubs. Full `drive` (create/move/delete) is an opt-in only if a
future `create` command is added.

## Troubleshooting

- **`email: None` / "insufficient authentication scopes"** — setup isn't complete (you're on the ADC
  fallback). Do the steps above.
- **`invalid_grant` on refresh** — the refresh token was revoked/expired. Re-run `auth login`
  (add `--force` to discard the old token first).
- **Keeps expiring after ~7 days** — the OAuth consent screen is in *Testing*; publish it
  *In production*.
- **Wrong Google account** — `auth status` shows the authenticated email; a stub records its own
  `email`. Re-run `auth login` signed into the right account if they differ.

## Security

`secrets/` is git-ignored (`token.json`, `token.backup.json`, `credentials.json`, `backups/`).
Never print or commit these. The tool writes tokens atomically (`os.replace`) so a crash never
leaves a half-written credential.
