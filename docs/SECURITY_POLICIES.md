# Security Policies — RAGbot

> Operational policies that complement [SECURITY.md](SECURITY.md). Where
> SECURITY.md describes **what is enforced**, this file describes **how
> long, by whom, and what to do when the policy fires**.
>
> **Audience**: ops + security reviewer.
> **Scope**: audit retention, GDPR right-to-be-forgotten, tenant deletion
> cascade, JWT key rotation summary.
> **Last review**: 2026-05-01 (Y4 hardening pass).

---

## 1. Audit log retention

### 1.1 What we log

| Stream | Purpose | PII surface |
|---|---|---|
| `audit_log` table | RBAC trail (who did what to which tenant entity) | actor `user_id`, target `record_*_id`, action verb, before/after JSON |
| `request_steps` table | Per-step pipeline latency for debugging | redacted query, citations, no raw user input |
| `request_logs` table | Per-request `connect_id` + token usage | `connect_id` (external user id), token counts |
| Redis Stream events | Async fan-out for analytics | post-redactor payloads only |
| Application logs (structlog) | Structured operational logs | post-redactor payloads only |

### 1.2 Retention defaults

| Stream | Default retention | Configurable via | Purge mechanism |
|---|---|---|---|
| `audit_log` | **90 days** | `system_config.audit_log_retention_days` | Cron job: `scripts/purge_audit_log.py` (TODO — see §6) |
| `request_steps` | **30 days** | `system_config.request_steps_retention_days` | Same cron |
| `request_logs` | **180 days** (billing trail) | `system_config.request_logs_retention_days` | Same cron |
| Redis Stream events | 7 days (Stream `MAXLEN`) | `system_config.event_stream_max_age_s` | Redis-side trimmer |
| Application logs | 14 days (file rotation) | `logrotate.conf` | OS logrotate |

**Why 90 days for audit_log?** GDPR Art. 5(1)(e) requires retention "no
longer than necessary"; 90 days covers most incident-response windows
(MTTD + MTTR + investigation buffer) without becoming a long-tail PII
liability. Shorter defaults are acceptable; longer requires DPO sign-off.

### 1.3 Purge job spec

A purge cron MUST:

1. Run nightly at 02:00 UTC.
2. Process tables in size order (largest first) to keep wall-time
   bounded — `request_steps` → `request_logs` → `audit_log`.
3. Batch DELETE in chunks of `DEFAULT_AUDIT_PURGE_BATCH_SIZE`
   (constant, default 5_000 rows) with `LIMIT ... RETURNING id` to
   avoid holding the autovacuum back.
4. Emit a structured event `audit.purge.completed.v1` with rows-deleted
   counts per table.
5. Hard-fail if any single batch exceeds 60 s — operator must intervene.

### 1.4 Excluded data

The purge MUST NOT delete:

- Legal-hold flagged rows (`audit_log.legal_hold = true`).
- Active subscription billing trail (`request_logs` rows tied to an
  unfinalised invoice).
- Per-tenant compliance overrides (`tenants.audit_retention_days_override`).

---

## 2. GDPR right-to-be-forgotten

### 2.1 Erase entry point

```bash
POST /admin/gdpr/erase
Authorization: Bearer <super_admin_token>
Content-Type: application/json

{
  "tenant_id": 1,
  "connect_id": "user-to-erase"
}
```

Permission: `gdpr:erase` (super_admin only — level 100). No tenant_admin
self-service path (Y4 verdict: too high blast radius for tenant_admin
to delete cross-user data).

### 2.2 Cascade

The erase endpoint runs in a single transaction:

1. **Soft-delete** all `conversations` rows where
   `(record_tenant_id, connect_id) = (:tid, :cid)` —
   sets `deleted_at = NOW()`. Cascades via FK to `messages`.
2. **Anonymise** `request_logs` — replace `connect_id` with
   `'gdpr-erased-{record_request_id}'`, NULL `query_text` and any free-text columns.
3. **Soft-delete** any cached `semantic_cache` rows scoped to that user.
4. **Emit** `gdpr.erase.completed.v1` event for downstream analytics
   purge (cohort tables, etc.).

### 2.3 Hard-delete window

After 30 days post-soft-delete, a follow-up cron hard-deletes the rows.
30-day window is the GDPR DPA-recommended grace period for accidental
deletes + legal-hold checks.

---

## 3. Tenant deletion cascade

See [plans/260501-Y4-TENANT-DELETION-CASCADE/plan.md](../plans/260501-Y4-TENANT-DELETION-CASCADE/plan.md)
for the implementation roadmap. Summary:

- **Soft-delete**: `tenants.deleted_at` set; all child resources
  filtered out by every repository (RLS + base class).
- **Hard-delete after 30 days**: cron runs `DELETE` cascade; vector store
  rows purged via `record_tenant_id` filter (not `record_bot_id`, since
  bots are deleted first).
- **Test invariant**: a deleted tenant's `record_bot_id` documents must
  NOT appear in any other tenant's retrieval result. Red-team test
  asserts `WHERE record_tenant_id = <deleted>` returns zero rows from
  the live `documents` + `document_chunks` + `semantic_cache` tables.

---

## 4. JWT key rotation

See [JWT_KEY_ROTATION.md](JWT_KEY_ROTATION.md) for the full playbook.

Headline rules:

- **Rotation cadence**: every 30 days for HS256 service tokens; every
  90 days for RS256 user tokens.
- **Overlap window**: 7 days during which the verifier accepts both
  the new and previous keys.
- **Emergency rotation** (compromised key): `<2 h` SLO from detection
  to old-key revocation, with `token_version` bump in Redis to
  invalidate every issued token instantly.

---

## 5. PII redactor coverage

The `VnRegexPiiRedactor` (Strategy: `vn_regex`) masks the following
classes before any payload hits the log/event stream or the LLM
context window:

| Class | Pattern source | Y4 added? |
|---|---|---|
| `EMAIL` | `PII_REGEX_EMAIL` | no (baseline) |
| `PHONE` (VN, contiguous + spaced + dotted) | `PII_REGEX_PHONE_VN*` | no (baseline) |
| `CCCD` (12-digit + 4-4-4 spaced) | `PII_REGEX_CCCD*` | no (baseline) |
| `API_KEY` (sk-/AIza/xox*-/generic 16+) | `PII_REGEX_API_KEY_*` | **yes** |
| `JWT` (3×base64url segments) | `PII_REGEX_JWT` | **yes** |
| `DSN` (postgres/mysql/mongo/redis with inline pw) | `PII_REGEX_DB_DSN` | **yes** |
| `CARD` (13–19 digit credit card) | `PII_REGEX_CREDIT_CARD` | **yes** |
| `VN_PLATE` (biển số xe) | `PII_REGEX_VN_PLATE` | **yes** |

Production deployments SHOULD swap to `presidio` for higher recall on
free-text PII (NER-driven). The Strategy registry already wires
`presidio` as a provider name; install `presidio-analyzer` to activate.

---

## 6. Open work (deferred to follow-up plans)

| Item | Plan path | Owner |
|---|---|---|
| Cron purge job for `audit_log` / `request_steps` | `plans/260501-Y4-AUDIT-PURGE-CRON/` (TODO) | ops |
| `python-jose` → `authlib` migration | `plans/260501-Y4-JWT-LIB-MIGRATION/` (TODO) | platform |
| Tenant deletion cascade alembic | `plans/260501-Y4-TENANT-DELETION-CASCADE/plan.md` | platform |
| GDPR DPA template | external (legal team) | DPO |

---

## 7. Incident playbook references

- Credential leak found in tracked file → [SECURITY.md §Scrub workflow](SECURITY.md#scrub-workflow-if-violation-found)
- Cross-tenant data leak suspected → [SECURITY.md §3-key identity](SECURITY.md#3-key-identity--the-foundation)
- JWT key compromise → [JWT_KEY_ROTATION.md §Emergency rotation](JWT_KEY_ROTATION.md#5-emergency-rotation-compromised-key)
- Rate-limit anomaly → SECURITY.md §Rate limiting (fail-closed)
