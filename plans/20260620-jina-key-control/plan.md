# [T2-CostPerf] Provider API-key registry + per-key rate-limit control

**Goal**: prod-grade, leader-tunable per-key rate limiting for embedding (and any
provider). Add per-key TPM/concurrency settings to the existing `api_keys`
table, an admin API to provision keys + tune limits in prod (no deploy), and
wire the round-robin pool + embedder to enforce **per-key** ceilings so no single
key is spammed past its provider quota (e.g. Jina free = 100k TPM / 2-concurrent
*per key*; N keys → N× headroom).

**Trigger**: `test-từ-đầu` re-ingest surfaced Jina 429 (`100,551/100,000 TPM`).
Verified root cause: 2 Jina keys are INDEPENDENT accounts, the pool already
round-robins (`ApiKeyPool.get_active`), but the TPM limiter is a single GLOBAL
bucket (180k) — it does not guarantee per-key ≤100k, so under concurrent load
one key overran while the other idled. Fix = per-key limiter, sized from
DB-stored per-key limits.

**Stance**: EVOLVE the existing `api_keys` table (empty, purpose-built) + the
`ApiKeyPool` round-robin. No new table, no rewrite.

---

## Analysis / case study (the control model — so this never recurs)

**Problem**: a provider (jina, openai/"chatgpt", …) enforces quota **per key**
(Jina free = 100k TPM + 2-concurrent per key). One key alone can't carry prod
traffic; spamming one key → 429 → dropped embeddings → docs `failed` → bot dark.

**Control model** (homogeneous pool per provider — "chọn jina thì cả pool là
jina, không trộn loại"):
1. **N keys of the SAME provider** in a pool (`api_keys` filtered by
   `provider_code` + active status). Adding a key = +1× quota, linearly.
2. **Round-robin** spreads requests evenly across the pool's healthy keys
   (`ApiKeyPool.get_active` rotates `_rr_index`, skips unhealthy) → no single key
   is spammed.
3. **Per-key limiter** (TPM + concurrency) paces each key under ITS own quota, so
   even uneven bursts never push a key past its provider cap.
4. **Per-key health/status** — a key that 429s (transient) → `cooldown` (Redis
   TTL, auto-recovers); a key that returns billing/auth failure (402/401/403,
   "hết tiền"/revoked) → `exhausted`/`error` (persistent) + the error message is
   recorded; the pool **skips** unhealthy keys until a human re-enables or
   cooldown lifts. When ALL keys are unhealthy the pool surfaces it loudly.
5. **Leader control via API**: add/update/disable keys, tune per-key limits, SEE
   each key's status + last error → fix prod without deploy.

This makes the rate-limit failure **self-isolating** (one bad key never takes the
provider down) and **observable** (status + message), so the `test-từ-đầu` 429
class of failure cannot silently recur.

---

## Security invariants (binding)
- Raw secret key value → stored **encrypted** (`api_keys.value_encrypted`, reuse
  existing crypto). NEVER `value_plain` for real keys, NEVER logged, NEVER in a
  tracked file. API accepts the raw key in the request body (TLS) → encrypts →
  stores → returns only a fingerprint + label, never the raw value back.
- Admin API gated `require_min_level(admin)`; tenant-scoped where applicable
  (provider keys are platform-level → admin-only).
- Domain-neutral: no brand/provider literal hardcoded beyond `provider_code`
  strings already in use.

## Phase 1 — Per-key TPM limiter in the embedder (unblocks restore)  [code+test]
Files: `src/ragbot/infrastructure/embedding/jina_embedder.py`,
`tests/unit/test_jina_embedder_per_key_tpm.py`.
- Replace the single global `TpmRateLimiter(per_key×n_keys×safety)` with a
  **per-key limiter map**: `_limiter_for(key)` lazily builds
  `TpmRateLimiter(tpm_per_key × safety)` per key. `_post_embed` acquires the
  limiter for the resolved key (the key is already passed in).
- **Round-robin per BATCH** (verify `embed_batch` resolves the key per call, not
  once per doc — if per-doc, move `_resolve_key()` into the batch loop so a big
  doc spreads across keys).
- `tpm_per_key` + `safety_fraction` become constructor args (default = existing
  constants) so Phase 2 can feed DB values.
- Test: 2 keys, fire 3× the per-key budget → assert pacing keeps each key's
  acquired tokens ≤ per-key ceiling (no key exceeds its bucket); round-robin
  spreads across keys.

## ⚠ Phase 2 BLOCKER discovered (2026-06-20) — two-table drift, reconcile FIRST
There are **two empty key tables**:
- **`ai_keys`** (cols: `api_key_encrypted, fingerprint, status, is_default,
  last_health_check_at, last_health_status, last_used_at, rotated_at,
  rotated_by_user_id`) — read by `DBBackedApiKeyPoolFactory._load_db_keys`
  (the SELECT joins `ai_keys`→`ai_providers`, filters `status='active'`) AND by
  `ProviderKeyResolver`. **This is the canonical pool/resolver table** + it
  already has `status` + health columns.
- **`api_keys`** (cols: `provider_code, label, value_plain, value_encrypted,
  active, rotation_state`) — targeted by the admin routes
  `GET/PUT/DELETE /admin/api-keys`.

→ **Admin writes `api_keys`; the pool reads `ai_keys`.** They are disconnected,
so "add a key via the API" would never reach the embedder pool. Phase 2 MUST
first **reconcile to ONE canonical table** (recommend `ai_keys` — pool/resolver
already use it + it has status/health), point the admin routes at it, drop/alias
the other, THEN add `tpm_limit`/`max_concurrent`/`last_error_message`/
`last_error_at` + wire the limits into the embedder. Doing this carefully (it
touches secrets + two route sets + the pool) is the next focused increment — NOT
to be rushed onto the drift.

Phase 1 (per-key limiter, committed e17c0f4) already fixes the 429 recurrence
independently of this table work (validated: thong-tu re-ingested 549 chunks,
null_leaf=0, **0 Jina 429s**). So Phase 2 is ops-control/observability, not a
blocker for correctness.

## Phase 2 — `ai_keys` registry columns + admin API + pool-from-DB  [migration+code+test]
Files: alembic migration, `models.py` (ApiKeyModel), `api_key_repository.py`,
`routes/admin/provider_keys.py`, `ApiKeyPoolFactory` (DB source), bootstrap wiring, tests.
- **Migration**: add to `api_keys`: `tpm_limit INT NULL`, `max_concurrent INT NULL`,
  `tier VARCHAR(16) NULL` (free/pro), `priority INT NOT NULL DEFAULT 0`,
  **`status VARCHAR(16) NOT NULL DEFAULT 'active'`** (active/cooldown/exhausted/
  error/disabled), **`last_error_message TEXT NULL`**, **`last_error_at TIMESTAMPTZ NULL`**,
  **`last_used_at TIMESTAMPTZ NULL`**. NULL limit → fall back to constant default.
- **Model**: add the columns to `ApiKeyModel`.
- **Status state machine**: `active` ⇄ `cooldown` (429 transient, auto-clears on
  Redis TTL expiry) ; `active` → `exhausted` (402 billing / quota-end) /
  `error` (401/403 auth/revoked) — persistent, set with `last_error_message` +
  `last_error_at`; `disabled` (manual). Pool serves only `active` keys.
- **Repo**: `ApiKeyRepository` — `add`, `list_by_provider`, `set_limits`,
  `set_status(id, status, error_message=None)`, `set_active`, `soft_delete`,
  `touch_used`. Encrypt on `add`.
- **Failure classification** (where the embedder/pool catches a provider error):
  map HTTP → status: `429`→cooldown, `402`→exhausted, `401/403`→error, else
  leave active (transient network). Persist status + message so the leader sees
  WHY a key died. Reuses existing Redis cooldown for the transient path.
- **Admin API** (`/api/ragbot/admin/provider-keys`, header-versioned, `admin`):
  - `POST` add key `{provider_code, label, value, tpm_limit?, max_concurrent?, tier?}`
  - `GET` list → `{id, label, fingerprint, provider_code, status, tpm_limit,
     max_concurrent, last_error_message, last_error_at, last_used_at}` — **NEVER raw value**
  - `PATCH /{id}` set limits / status / re-enable
  - `DELETE /{id}` soft-delete
- **PoolFactory**: load `active` keys + per-key limits from `api_keys` for a
  provider; **fallback to env `PROVIDER_API_KEYS_JSON`** when the table is empty
  (backward-compat, zero-downtime). Embedder builds per-key limiters from each
  entry's `tpm_limit` (or default).
- Tests: repo CRUD + encryption round-trip; status state-machine transitions;
  failure→status mapping (429/402/401); API RBAC + no-raw-leak; pool prefers DB
  then env + skips non-active; embedder sizes limiter from DB limit.

## Phase 3 — Restore + test RAG properly  [ops+eval]
- Re-ingest the 3 bots **serially** (1 doc at a time, dev/free-safe) → thong-tu
  back to `active`, spa/xe null leaves cleared (null_leaf=0).
- Run `scripts/debug_rag_8step.py --live` → COVERAGE / HALLU=0 / CHUNK_RECALL
  across all 3 bots = the "test chuẩn".
- Revert `bots.bypass_token_check` for the 3 bots.

## Out of scope (note for later)
- Brittle finalize policy (partial-embed → whole doc `failed`): defense-in-depth
  resilience fix — track separately; per-key limiter should prevent the 429s that
  trigger it.
- Hot-reload of limits without restart (Phase 2 applies on next pool refresh /
  restart; live hot-reload = follow-up).

## Verify (Quality Gate)
zero-hardcode (limits from DB/constants) · RBAC admin on API · secret never
logged/tracked/returned · per-key enforcement test (real assertions) · pool
backward-compat env fallback · T2 declared.
