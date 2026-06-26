# OneLife — Admin export / import

A hidden, admin-only panel to export and import game data. Reached via a **⚙ gear**
in the in-game top bar, shown only to admins.

## Who is an admin
Set `ONELIFE_ADMIN_EMAILS` (comma-separated) in `.env`; the logged-in account's
email must be in the list. `GET /api/admin/me` → `{is_admin}`; all `/api/admin/*`
endpoints return **403** otherwise. No DB reset needed (uses the existing `email` column).

## Scopes
| Scope | Export | Import | Notes |
|---|---|---|---|
| **Authored content** | `content-export.yaml` | — | **Export only.** The YAML content files are the single source of truth; to change content, edit the files and re-seed (seeding reconciles the DB to them). The in-app graph is a **read-only** view; dragging a node writes its `pos:` back into the YAML. |
| **Full database** | `onelife-db-export.json` | JSON | Every table incl. accounts. Import **truncates + restores all tables** in one transaction. **Contains secrets** (password hashes, encrypted TOTP, tokens) — treat the file as sensitive. |
| **Player save** | `onelife-save-<name>.json` | JSON | One player's runtime state. Import **overwrites that player's** state, onto the **same `player_id`** (no cross-account transfer). |

Destructive imports (full DB, player save) require typing **`REPLACE`** in the UI
(`confirm` field; the API rejects anything else with 400) and run in a transaction
(rollback on error).

## How it works
- Backend: `content.export_content()` (inverse of `seed_content`), `admin.py`
  (`export_all`/`import_all`, `export_player`/`import_player` via Postgres
  `json_agg` / `json_populate_recordset`, which preserves column types; the
  pgvector `embedding` column is exported as text and recast on import).
- Endpoints live in `main.py` under `/api/admin/*`, gated by `_require_admin`.
- Frontend: the ⚙ panel in `web/src/App.svelte`; download/upload via `web/src/lib/api.js`.

## Caveats
- Full-DB import assumes the dump matches the **current schema** (no migration);
  a dump from an older schema may fail.
- A full-DB import logs you out (sessions are replaced) — log back in.
