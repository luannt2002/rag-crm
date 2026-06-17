# Owner-Action Scripts

Operator-runnable scripts for the 3 bot-owner remediations identified at
the R7 verdict ("code path ceiling reached at this corpus").

## Scripts

| Script | Purpose |
|---|---|
| `01_re_embed_missing_chunks.sh` | Re-embed `document_chunks WHERE embedding IS NULL` for the configured bot — wakes the vector path. |
| `02_sysprompt_loosen.sh` | UPDATE `bots.system_prompt` from a file; auto-backup + Redis cache bust. Bot-owner content only — no sysprompt body in this repo. |
| `03_corpus_upload.sh` | Bulk POST `/api/ragbot/sync/documents` from a directory of `*.txt` / `*.md` files. UPSERT by `source_url` (idempotent). |
| `04_smoke_after_action.sh` | 5 generic VN smoke questions; asserts greeting not refused + chat OK. |
| `run_all.sh` | Orchestrate Step 1 -> 2 -> 3 -> 4 with `--yes` / `--dry-run` / per-step skip flags. |

## Required env

```
LOADTEST_BOT_ID=<bot-slug>
LOADTEST_TENANT_ID=<tid>
LOADTEST_CHANNEL_TYPE=<channel>
RAGBOT_TOKEN=<bearer admin/service token>
DATABASE_URL_SYNC=postgresql+psycopg2://...
RAGBOT_SYSPROMPT_PATH=/path/to/new_sysprompt.txt   # for 02 + run_all
RAGBOT_CORPUS_DIR=/path/to/corpus_dir              # for 03 + run_all
```

Optional: `RAGBOT_BASE_URL` (default `http://localhost:3004`),
`REDIS_URL`, `PYTHON_BIN`, `OWNER_ACTION_LOG_DIR` (default `/var/log/ragbot`),
`SMOKE_TOP_SCORE_MIN` (default `0.20`), `REEMBED_BATCH_SIZE` (default `32`).

## Recommended order

```bash
set -a && source .env && set +a
export LOADTEST_BOT_ID=... LOADTEST_TENANT_ID=... LOADTEST_CHANNEL_TYPE=...
export RAGBOT_TOKEN=... RAGBOT_SYSPROMPT_PATH=... RAGBOT_CORPUS_DIR=...
scripts/owner_action/run_all.sh --yes
```

For step-by-step preview:
```bash
scripts/owner_action/run_all.sh --dry-run
```

## Idempotency

All scripts are idempotent:
- 02 — re-applying the same sysprompt file is a no-op aside from `updated_at` bump.
- 03 — `wipe_existing=false` UPSERTs by `source_url` (no duplicates).
- 01 — only writes where `embedding IS NULL`; re-running on already-embedded chunks exits 0.
- 04 — read-only.

## Logs + audit

- Per-step log: `${OWNER_ACTION_LOG_DIR}/owner_action_<step>_<ts>.log`
- Run-all dir: `${OWNER_ACTION_LOG_DIR}/owner_action_<ts>/`
- Server-side `audit_log` rows for sysprompt UPDATE + corpus ingest.

## Detail

See `reports/MEGA_OWNER_ACTION_RUNBOOK_20260430.md` for full pre-conditions,
rollback, common errors, and SQL queries.
