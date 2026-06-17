# P2-J — OPS / SLO / DR / SECURITY-COMPLIANCE (D11) — Gap audit (Phase 2, STANCE = EVOLVE)

> Auditor P2-J. Date 2026-06-10 · branch `fix-260604-action-slotmachine-dead-key` · alembic head 0195.
> READ-ONLY src/alembic/tests. Only this file written. Every claim = `file:line` / alembic / commit / git-grep / web-source.
> Labels: ✅ ĐÃ CHUẨN · 🕰 evolve-to-2026-std · ↔️ doc≠code · 🐛 hole.
> Builds on P2-C (RLS inert + superuser DSN) + P2-F (exactly-once/DLQ/reaper). Charter D11 = SLO + alerting + backup/DR + secrets rotation + **PDPD** compliance, guard_output PII.
>
> **Headline:** the OPS/SECURITY surface is the **most mature** layer audited so far — real hash-chain audit trail, real fail-closed IP rate-limit, real per-tenant CORS, real anti-abuse, real DR docs + `pg_dump` script, AES-GCM secret decryption, JWT versioned revocation, webhook secret rotation with grace period. The gaps are **(1)** PDPD legal target is itself stale (charter says "Nghị định 13" but the law changed 2026-01-01), **(2)** `api_keys.value_plain` stores provider keys in **plaintext** in the DB, **(3)** no automated SLO-breach / cost-cap **alert scheduler** in-repo (the evaluator exists but only an offline script calls it), **(4)** DR doc promises **WAL/PITR RPO=5min** but the only shipped backup is a **nightly pg_dump** (doc≠code). Most remaining items are **ops-side** (cron, S3, KMS) not code-side.

---

## (1) LABELED COMPONENT TABLE

| Component | Label | Evidence (`file:line` / alembic / web) | One-line verdict (D11 axis) |
|---|---|---|---|
| **audit hash-chain (writer)** | ✅ ĐÃ CHUẨN | `audit_log_hasher.py:93-135` sha256(prev_hash‖US‖fields); bit-stable w/ alembic `010g` backfill `:10` | KIỂM SOÁT: tamper-evident, bit-identical SQL↔Python. **Đừng đụng.** |
| **audit hash-chain (verifier)** | ✅ ĐÃ CHUẨN | `audit_verifier.py:68-148`: per-tenant scan, `::text` JSONB to dodge asyncpg lossy round-trip `:91-94`, continues on mismatch `:139-142` | KIỂM SOÁT: real recompute, not assert-True. |
| **GDPR erase + audit fan-out** | ✅ (mostly) | `admin_gdpr.py:48-114` erase message → soft_delete_content + scrub request_logs + **2 audit rows**; RBAC level-80 `:27-28`; tenant-scoped `:56` | AN TOÀN: every erase leaves forensic trail. See 🕰-PDPD for legal-target drift. |
| **PII redaction at boundary (guard_input)** | ✅ ĐÃ CHUẨN | `local_guardrail.py:142-183` `pii_vi`(phone/email/cmnd)/`pii_en`(ssn) → `action="redact"`; `details` JSONB stores **match_count only, never raw text** `:20,:167` | AN TOÀN: claude-mem boundary-redaction pattern honoured — PII metadata-only persisted. |
| **system-prompt-leak + secret-scanner (guard_output)** | ✅ | `local_guardrail.py:296-356` shingle-hash leak + `secret_scanner` regex → severity=block | AN TOÀN: output egress guarded. |
| **IP rate-limit fail-CLOSED** | ✅ ĐÃ CHUẨN | `ip_rate_limit.py:181-208` Redis-missing/error → 503 (not fail-open); no `X-RateLimit-*` reveal `:269-284`; trusted-XFF only `:77-107` | AN TOÀN: anti-spray can't become DoS amplifier. SOTA-shaped. **Đừng đụng.** |
| **anti-abuse composite** | ✅ | `anti_abuse.py:140-286` UA denylist + auth-fail ban + scanner soft-throttle + 4xx-ratio → suspicious set; loadtest-bypass carve-out `:232-261` | AN TOÀN: 4-layer cheap heuristics, narrow excepts. |
| **honeypot fingerprinting** | ✅ | `honeypot.py:72-98` 404-mimic (indistinguishable) + suspicious-set flag; `include_in_schema=False` | AN TOÀN: passive SOC signal. |
| **per-tenant CORS deny-by-default** | ✅ ĐÃ CHUẨN | `cors_per_tenant.py:219-261` cache/DB error → `()` deny; wildcard host single-`*` only `:101-113`; `Vary: Origin` `:199-203` | AN TOÀN: per-tenant whitelist, fails safe. |
| **security headers (OWASP baseline)** | ✅ | `security_headers.py:113-136` nosniff/DENY/Referrer/CSP/Permissions/COOP/CORP/HSTS-gated | AN TOÀN: HSTS only when TLS-terminated (correct). |
| **body-size limit + chunked reject** | ✅ | `body_size.py:43-83` per-path cap + 411 on chunked-transfer (no streaming bypass) | AN TOÀN: pre-auth, pre-json.loads. |
| **JWT versioned revocation + iss/exp enforce** | ✅ | `jwt_token_service.py:91-97` `issuer=`+`require=JWT_REQUIRED_CLAIMS`; revoke emits outbox in same tx `:342-383` (cross-replica cache drop) | AN TOÀN: leaked-token kill-switch, no silent legacy-token accept. |
| **webhook HMAC secret rotation (grace)** | ✅ | `webhook_secret_rotation.py:159-175` versioned + grace-period verify chain `:12-19` | AN TOÀN: webhook secret rotates without breaking in-flight. |
| **AES-GCM secret decryption (KEK from env)** | ✅ (dev-tier) | `env_secrets.py:24-50` AESGCM(nonce12‖ct), KEK from `RAGBOT_CONFIG_KEK`; raises if KEK unset `:29-33` | AN TOÀN where used. KEK in env (not KMS) = 🕰 below. |
| **API-key hot-swap pool (active-passive cooldown)** | ✅ | `api_key_pool.py:56-148` Redis cooldown ledger, digest-only id (no key leak) `:14,:154-156` | RẺ/AN TOÀN: Jina-key-burn failover (memory `project_jina_key_supply`) is real. |
| **ProviderKeyResolver hot-rotate (no restart)** | ✅ | `provider_key_resolver.py:54-127` Redis 30s → `api_keys` (rotation_state='live') → env fallback | RẺ: admin PUT rotates in ≤30s. |
| **`tenant_hmac_secret` weak-value + length guard** | ✅ | `settings.py:374-397` rejects `change-me-in-prod`/<32char in strict envs | AN TOÀN: prod can't boot on placeholder secret. |
| **/metrics endpoint auth-gated** | ✅ | `app.py:578-592` 401 if metrics-auth missing (closed master-report Finding #3) | AN TOÀN: no public Prometheus scrape. |
| **health probe fail-soft (200 always)** | ✅ | `health.py:107-157` parallel pg+redis probe, status in body not HTTP code; `health_models.py` RBAC-80 `:459-460` | KIỂM SOÁT: orchestrator-safe liveness. |
| **error-notify hook wired into workers** | ✅ | `error_notify_hook.py:49-91` fire-and-forget, never breaks business logic; **wired** `chat_worker.py`/`document_worker.py`/`query_graph.py`/`chat_stream.py` (git-grep on_ai_error) | KIỂM SOÁT: post-retry/CB alert path is REAL, not stub. |
| **webhook dispatcher (real POST)** | ✅ | `webhook_dispatcher.py:28,109,134` httpx POST + `notify_dropped_total{reason}` metric | KIỂM SOÁT: alert egress is a real HTTP call. |
| **notify-channel resolver (DB→env→none)** | ✅ | `notify_channel_resolver.py:62-106` 60s cache, masked-for-log, invalidate on admin PATCH | KIỂM SOÁT: alert target hot-editable, secret masked. |
| **DR plan + backup script exist** | ✅ (doc+code present) | `docs/ops/DISASTER_RECOVERY.md` (RTO 30m, RPO 5m), `scripts/backup_db.sh` (pg_dump -Fc, fail-loud, atomic, retain) | KIỂM SOÁT: DR is documented + a real script ships. See 🐛-DR / ↔️-DR for the WAL gap. |
| **cost-cap evaluator (per-tenant token)** | ✅ logic / 🐛 unscheduled | `cost_cap_alerter.py:82-199` real GROUP-BY + warn/exceed severity + structlog event; **only caller = `scripts/audit_per_tenant_cost.py`** (git-grep), no worker/cron in-repo | RẺ: the SQL is correct; nothing runs it on a schedule. See 🐛-ALERT. |
| **SLO-breach alerting (p95 T1<1s/T2<3s/T3<15s)** | 🐛 LACK | charter axis NHANH; `admin_metrics.py:22-94` exposes p95 view-only (RBAC-60); **no burn-rate / threshold-breach alert** anywhere (grep `slo|error.budget|burn.rate` = 0 in src non-test) | NHANH: latency is *observable* (request_steps + /metrics) but **not alerted**. See 🐛-SLO. |
| **`api_keys.value_plain` = plaintext provider key in DB** | 🐛 (HIGH) | alembic `20260512_0086_api_keys_hot_swap.py:14-15,:42-43` "`value_plain` is plain-text. AES-GCM at-rest … is a **planned follow-up** (column `value_encrypted` **reserved**)"; resolver reads `value_plain` `provider_key_resolver.py:89` | AN TOÀN: provider API keys at rest in cleartext. `value_encrypted` exists but unused. See 🐛-KEY. |
| **WAL/PITR RPO=5min** | ↔️ doc≠code / 🐛 | DR doc `DISASTER_RECOVERY.md:33,71-75` promises `archive_command=aws s3 cp … archive_timeout=300`; **shipped code = nightly `pg_dump` only** (`backup_db.sh` cron `0 2 * * *`); `BACKUP_RESTORE_RUNBOOK.md` honestly says "last nightly dump" | KIỂM SOÁT: stated RPO 5m unachievable by shipped artifacts (real RPO = up to 24h). Ops-side WAL config absent from repo. See ↔️-DR. |
| **PDPD legal target = "Nghị định 13"** | 🕰 (stale) | charter/`00-charter.md` + register D11 cite "Nghị định 13"; **web 2026: Decree 13/2023 REPLACED by PDPL Law 91/2025/QH15 + Decree 356/2025/ND-CP, effective 2026-01-01** | AN TOÀN: compliance target name is one regime behind. Erase/consent mechanics still apply; see 🕰-PDPD. |
| **consent capture / withdraw mechanism** | 🐛 LACK | PDPL requires verifiable consent + easy withdraw (web); repo has erasure (`admin_gdpr.py`) + audit but **no consent-record table / withdraw endpoint** (grep `consent` in src = 0) | AN TOÀN: erasure ✅ but consent lifecycle absent. Likely ops/product-side but flagged. |
| **data export (right to portability/access)** | 🐛 LACK (low) | PDPL data-subject access; repo has erase + audit-read but no per-subject **export** endpoint | AN TOÀN: access-right unmet at code level. |
| **`health_models` env-key vs runtime DB-key drift** | ↔️ | `health_models.py:130-134` `_api_key_for`= `os.getenv(api_key_ref)`; `_detect_config_drift:398` `os.getenv`; **runtime uses `ProviderKeyResolver` (DB)** | KIỂM SOÁT: a key rotated **only in DB** (the documented hot-swap) → health probe reports `missing_api_key` though runtime works. Health drifts from runtime. See ↔️-HEALTH. |
| **KEK in env-var (not KMS/HSM)** | 🕰 | `env_secrets.py:21,28` KEK from `RAGBOT_CONFIG_KEK` env; module docstring self-labels "dev-mode" `:1,:16` | AN TOÀN: 2026 std = KEK in KMS/HSM (envelope). Env-KEK acceptable interim, flagged. |
| **govbot doc URL hardcoded in script** | 🐛 (low, non-core) | `scripts/loadtest_ingest_thongtu_govbot.py:89` literal Google-Docs URL | domain-neutral: not a secret/tenant-host, but a hardcoded corpus pointer in a tracked script. Non-core (scripts/), low. |

**Counts:** ✅ = 22 · 🐛 = 7 (1 HIGH=KEY, others LACK/low) · 🕰 = 3 · ↔️ = 3.
**No secret/DSN/brand-tenant literal leaked in tracked `.py/.sh/.yml`** — git-grep hits were all test fixtures (fake `sk-…`, `postgresql://u:p@h`), `os.getenv` refs, or Google-Docs URLs. `.env` correctly gitignored (`.gitignore:22-26`). **AN TOÀN secret-hygiene: PASS.**

---

## (2) 🐛 EACH HOLE + REPRO/VERIFY SKETCH

### 🐛-KEY — provider API keys stored plaintext (`api_keys.value_plain`) [HIGH]
- **Evidence:** alembic `20260512_0086_api_keys_hot_swap.py:14-15` self-documents "`value_plain` is plain-text. AES-GCM at-rest encryption is a planned follow-up commit (column `value_encrypted` reserved)". `provider_key_resolver.py:89` `SELECT value_plain`. The AES-GCM machinery **already exists** (`env_secrets.py:42-50` `encrypt()`) but the hot-swap table bypasses it.
- **Root cause:** two parallel key paths — the legacy `ai_providers.api_key_encrypted` (AES-GCM via `EnvSecretsAdapter`, ✅) vs the newer hot-swap `api_keys.value_plain` (cleartext, 🐛). The hot-swap convenience path dropped the encryption.
- **Verify sketch:** `psql -c "SELECT provider_code, label, length(value_plain) FROM api_keys WHERE value_plain IS NOT NULL"` → any non-null = a readable key for anyone with DB read (and P2-C proved the app connects as **superuser**, and pg_dump backups carry it — `backup_db.sh:38` comment even claims "PII/secrets in encrypted form" which is **false** for these rows).
- **EVOLVE fix (code-side, Phase 4):** write keys to the reserved `value_encrypted` via `EnvSecretsAdapter.encrypt`, resolver decrypts on read; backfill migration encrypts existing `value_plain` then nulls it. KEK already wired.

### 🐛-ALERT — cost-cap evaluator never runs on a schedule
- **Evidence:** `cost_cap_alerter.evaluate_tenants` (`:82`) is correct read-only SQL emitting `cost_cap_exceeded`/`cost_cap_warning` structlog events, but git-grep shows the **only** caller is `scripts/audit_per_tenant_cost.py` (an operator-run one-shot). No worker in `interfaces/workers/` invokes it; no cron in repo.
- **Impact:** a tenant blowing past `quota_monthly_tokens` is detected **only if an operator manually runs the script**. Charter RẺ axis ("cost/query per-tenant") is measurable but not *alerted*.
- **Verify sketch:** grep `evaluate_tenants` across `src/` → 0 hits → confirms no in-process scheduler.
- **EVOLVE fix:** code-side = a tiny periodic task (reuse `document_recovery_worker` cadence pattern) calling `evaluate_tenants` + routing events through the existing `WebhookNotifyDispatcher`. OR ops-side = cron the script. The dispatcher + resolver + events already exist — only the *scheduler* is missing.

### 🐛-SLO — no p95/latency SLO-breach alert
- **Evidence:** charter NHANH = p95 T1<1s/T2<3s/T3<15s. Latency IS captured (`request_steps`, `/metrics` Prometheus, `admin_metrics.py` p95 view). But grep `slo|error.budget|burn.rate|p95.*alert` in `src/` (non-test) = 0 — nothing fires when p95 crosses the charter target.
- **Impact:** the platform can silently breach its own latency SLO (P2-C/F already note p95 ~16-22s historically) with no alert.
- **Verify sketch:** confirm no alerting rule references the p95 target constants; confirm `error_notify_hook` only fires on *exceptions*, not on *latency-budget burn* (`error_notify_hook.py:94-106` maps error→severity, no latency path).
- **EVOLVE fix:** code-side periodic SLO-eval (mirror cost-cap shape) reading `request_logs` latency percentiles vs `DEFAULT_SLO_*` constants → `WebhookNotifyDispatcher`. 2026-std = **burn-rate** alerting (see 🕰-SLO), not raw-threshold spam.

### 🐛-CONSENT / 🐛-EXPORT — PDPL consent-lifecycle + data-export absent
- **Evidence:** PDPL (web 2026) mandates verifiable consent + easy withdrawal + subject access/export. Repo has **erasure** (`admin_gdpr.py`) + audit but grep `consent` / per-subject `export` in `src/` = 0.
- **Impact:** erasure (right-to-be-forgotten) ✅; consent-capture, consent-withdraw, and data-access/export ❌. For B2B-VN GA these are required.
- **Note:** much of this is **product/ops-side** (consent is captured by the bot-owner's front-end, not necessarily the RAG platform), but the platform needs at minimum a consent-record table + withdraw hook if it is the data controller. Flag for D11 + Wave 6 product scope.

### ↔️-DR — WAL/PITR promised, only nightly pg_dump shipped
- **Evidence:** `DISASTER_RECOVERY.md:33` RPO "5 min … last WAL segment in off-host bucket" + `:71-75` `archive_command=aws s3 cp … archive_timeout=300` + `pg_basebackup` weekly. **Shipped code:** `backup_db.sh` = nightly `pg_dump -Fc` to a **local** dir (`:36` cron `0 2 * * *`, `:23` `RAGBOT_BACKUP_DIR` default `/var/backups/ragbot`), no off-host ship, no WAL. `BACKUP_RESTORE_RUNBOOK.md` is honest: "Recovery point: last nightly dump" + "Off-host shipping … operator policy; this runbook only writes to the local backup directory".
- **Real RPO = up to 24h** (last nightly dump), not 5 min. The WAL/PITR layer is **ops-side config absent from repo** (`postgresql.conf` archive_command is a server setting, not app code).
- **Verify sketch:** on the prod host, `psql -c "SHOW archive_mode"` → if `off`, RPO-5min is fiction. Check the backup bucket for WAL segments ≤10min old (DR doc's own self-check `:75`).
- **EVOLVE fix:** ops-side = enable `archive_mode=on` + `archive_command` to off-host bucket OR adopt pgBackRest (2026-std, see 🕰-DR). Code-side delta = none required; reconcile the DR doc's RPO claim to match shipped reality until WAL is wired.

### ↔️-HEALTH — health-models probes env-key, runtime uses DB-key
- **Evidence:** `health_models.py:130-134,398` resolve the provider key via `os.getenv(api_key_ref)`. Runtime hot-path resolves via `ProviderKeyResolver` (DB `api_keys` → env fallback). A key rotated **only in DB** (the whole point of hot-swap) makes the health probe report `missing_api_key`/`config_drift` while live traffic succeeds — a false-red that erodes trust in the deploy gate.
- **Verify sketch:** rotate a key via `PUT /admin/api-keys/{code}` (DB only, no env), then `GET /health/models` → reranker/llm probe shows `missing_api_key` though a real chat works.
- **EVOLVE fix (code-side, low):** health-models should resolve through `ProviderKeyResolver` (same chain as runtime) so the probe mirrors production truth.

---

## (3) 🕰 LỖI THỜI — 2026 standard + source + verdict

### 🕰-PDPD — compliance target name is stale (Decree 13 → PDPL 2026)
- **2026 reality:** Decree 13/2023/ND-CP (the charter's "Nghị định 13") was **superseded by Law 91/2025/QH15 (PDPL) + Decree 356/2025/ND-CP, effective 2026-01-01**. The PDPL keeps a **consent-centric** model (voluntary, explicit, easy withdrawal — silence ≠ consent) and grants **right-to-erasure** but **drops the strict 72-hour** completion deadline.
- **Verdict for Ragbot:** the *mechanics* the charter cares about still hold — erasure (`admin_gdpr.py` ✅), boundary PII-redaction (`local_guardrail.py` ✅), tamper-evident audit (`audit_verifier` ✅). The gaps vs 2026-PDPL are **consent-lifecycle + data-export** (🐛-CONSENT/EXPORT), not the erasure path. **EVOLVE:** keep the strong erasure+audit core; add consent-record + withdraw + export for GA; update charter/register D11 to cite **PDPL 91/2025 + Decree 356/2025** not "Nghị định 13". ([Hogan Lovells](https://www.hoganlovells.com/en/publications/vietnam-enacts-landmark-law-on-personal-data-protection-stable-standing-with-stricter-compliance) · [Future of Privacy Forum](https://fpf.org/blog/vietnams-personal-data-protection-decree-overview-key-takeaways-and-context/) · [Tilleke & Gibbins](https://www.tilleke.com/insights/vietnams-new-personal-data-protection-law-a-closer-look/))

### 🕰-SLO — 2026 std = burn-rate alerting, not raw-threshold
- **2026 std:** "Alerting works best when tied to **SLO burn rates** rather than raw thresholds — reduces noise while catching sustained issues early." Start minimal: tokens, cost, **p95 latency, error-rate** alerts. The 3am dashboard must answer "is everything OK?" in <10s (current status: error rate, p95, cost/hr). TTFT <2s p95, total <30s p99 are common targets.
- **Verdict:** Ragbot has the *signals* (request_steps, /metrics, p95 views) but **no alert layer at all** (🐛-SLO). **EVOLVE:** add a thin SLO-eval task feeding the existing dispatcher; prefer burn-rate over raw-threshold to avoid noise. Do NOT add a Prometheus collector/alertmanager stack (memory `feedback_no_premature_observability` — aggregate via structlog + existing audit events). ([OneUptime LLM latency monitoring](https://oneuptime.com/blog/post/2026-01-30-llmops-latency-monitoring/view) · [Sentry LLM KPIs](https://blog.sentry.io/core-kpis-llm-performance-how-to-track-metrics/) · [OpenObserve SLO-based alerting](https://openobserve.ai/blog/slo-based-alerting/))

### 🕰-DR + KEK — 2026 std = pgBackRest WAL/PITR + envelope-encryption KEK in KMS
- **2026 std (backup):** "An untested backup is not a backup — it is a hope." Production = **pgBackRest** (or Barman): full weekly + differential daily + **continuous WAL archiving** to a **dedicated off-host repo** (S3/GCS/Azure) with `repo-cipher-type=aes-256-cbc`. PITR replays WAL to any minute. **2026 std (secrets):** **envelope encryption** — DEK encrypts data, KEK in **KMS/HSM**, automated rotation + key versioning for old-data decrypt.
- **Verdict:** Ragbot's `pg_dump` nightly is the *minimum viable* tier; the DR doc already names the WAL target but it isn't wired (↔️-DR). KEK-in-env (`env_secrets.py`) is the dev-tier of envelope encryption — the *shape* is right (KEK/DEK split), the *KEK custody* should move to KMS for GA. **EVOLVE (both mostly ops-side):** adopt pgBackRest + off-host bucket (ops); migrate KEK to KMS (ops) — code already does envelope-style decrypt, so the `EnvSecretsAdapter` Port just needs a `KmsSecretsAdapter` sibling (1 file, registry-swap, consistent with charter Port/Adapter). ([Stormatics pgBackRest DR](https://stormatics.tech/blogs/disaster-recovery-guide-with-pgbackrest) · [PostgreSQL PITR docs](https://www.postgresql.org/docs/current/continuous-archiving.html) · [DEV envelope/KMS best practice](https://dev.to/sudoconsultants/securing-workloads-with-aws-kms-and-encryption-best-practices-2lh1))

---

## (4) ANSWERS TO THE 6 QUESTIONS

**Q1 — PDPD/PDPL compliance: what does `admin_gdpr.py` actually do? PII at boundary? guard_output PII? audit hash-chain?**
- **Erasure: REAL.** `admin_gdpr.py:48-114/117-171` soft-deletes message/conversation content + scrubs request_logs PII for the conversation, RBAC-80, tenant-scoped, emits **forensic audit rows** per action. Note: `scrub_pii_for_conversation` (`request_log_repository.py:202-232`) is now mostly a **count** because the PII JSONB column was **dropped at alembic 0109/G15** — i.e. the platform stopped persisting chunk-preview PII entirely (good).
- **PII at boundary: YES.** `local_guardrail.py:142-183` redacts VN phone/email/CMND + EN SSN at **guard_input** and persists **metadata only, never raw text** (`:20,:167`) — the claude-mem boundary pattern. guard_output catches system-prompt-leak + secret-leak (`:296-356`).
- **Audit integrity: REAL hash-chain.** `audit_log_hasher`+`audit_verifier` recompute sha256(prev‖fields), bit-stable with alembic `010g`, `/audit/verify` endpoint (RBAC-60). Tamper (UPDATE/DELETE/retro-INSERT) → mismatch.
- **Gap:** legal target is **stale** (Decree 13 → PDPL 2026, 🕰-PDPD) and **consent-lifecycle + data-export absent** (🐛-CONSENT/EXPORT).

**Q2 — SLO defined + alert on breach? cost_cap/error_notify/notify_channel real or stub? alert path?**
- **SLO targets defined** in charter + DR doc (p95 T1/T2/T3, RTO 30m, RPO 5m) but **no breach-alerting** in code (🐛-SLO).
- **error_notify_hook = REAL + wired** (`chat_worker`/`document_worker`/`query_graph`/`chat_stream`), fires post-retry/CB, fire-and-forget, never breaks business logic.
- **webhook_dispatcher = REAL** httpx POST with `notify_dropped_total` metric; **notify_channel_resolver = REAL** DB→env→none with 60s cache + masked secret + invalidate-on-write.
- **cost_cap_alerter logic = REAL** but **unscheduled** — only an offline script calls it (🐛-ALERT).
- **Alert path:** error → `ErrorNotifyHook.on_ai_error` → `WebhookNotifyDispatcher.dispatch` → resolve channel (DB/env) → httpx POST. Channel resolves to the operator-configured webhook (`system_config` row or env). This path is production-grade for **errors**; missing for **SLO/cost** scheduling.

**Q3 — Backup/DR PostgreSQL: pg_dump/WAL/PITR evidence?**
- **pg_dump: YES, shipped.** `scripts/backup_db.sh` — `pg_dump -Fc`, fail-loud, atomic temp-rename, retention, `pg_isready` preflight, cron-documented (nightly). Plus `docs/ops/DISASTER_RECOVERY.md` (RTO/RPO/SEV plan) + `BACKUP_RESTORE_RUNBOOK.md` + `RLS_ACTIVATION_RUNBOOK.md`.
- **WAL/PITR: documented but NOT wired (↔️-DR).** DR doc promises archive_command + archive_timeout=300 + pg_basebackup; the shipped artifact is nightly-dump-only to a **local** dir. Real RPO ≈ 24h, not the stated 5min. WAL config is **ops-side** (`postgresql.conf`), absent from repo — **GIẢ THUYẾT: not found in repo, must be verified/enabled at the DB host.**

**Q4 — Secrets rotation: provider key + JWT? DB-backed pool? JWT secret rotation?**
- **Provider-key rotation: REAL.** `ProviderKeyResolver` hot-swap (DB→cache, ≤30s flip, no restart) + `ApiKeyPool` active-passive cooldown failover (Jina-burn playbook is real). `webhook_secret_rotation` = versioned HMAC + grace-period.
- **But provider keys stored PLAINTEXT** at rest (`api_keys.value_plain`, 🐛-KEY HIGH) — the `value_encrypted` column is reserved-but-unused; AES-GCM machinery exists but this path skips it.
- **JWT: token-version rotation REAL** (`jwt_token_service` regenerate→version++ → old token rejected pre-exp, cross-replica via outbox). **But the JWT signing SECRET (`tenant_hmac_secret`) has NO rotation mechanism** — single static env secret (weak-value/length guarded at boot, `settings.py:374-397`, but no key-versioned signing rotation). For GA, a signing-key rotation (kid-based, dual-verify grace) is the 2026 gap.

**Q5 — security headers / anti-abuse / honeypot / rate-limit / CORS — 2026-adequate?**
- **YES, this is the strongest layer.** Security headers (OWASP baseline, HSTS TLS-gated), anti-abuse (UA + auth-fail-ban + scanner-throttle + 4xx-ratio), honeypot (404-mimic + suspicious-flag), IP rate-limit (**fail-CLOSED**, trusted-XFF-only, no cap-reveal), source rate-limit (per-tenant×source-tag fair-queue), per-tenant CORS (**deny-by-default**, single-`*` wildcard). All zero-hardcode + domain-neutral + narrow-except. **Đừng đụng.** Minor: IP RL is in-app (operator-edge WAF/Cloudflare would be belt-and-suspenders, ops-side).

**Q6 — secret-literal leak in tracked files?**
- **NONE.** git-grep for DSN/password/`sk-…`/Bearer/brand-host across tracked `.py/.sh/.yml/.yaml/.toml` → all hits are **test fixtures** (fake `postgresql://u:p@h`, `sk-abcdef…`), `os.getenv` references, or audit-grep patterns themselves. `.env` correctly gitignored (`.gitignore:22-26`). One low/non-core flag: `scripts/loadtest_ingest_thongtu_govbot.py:89` hardcodes a Google-Docs corpus URL (not a secret/tenant-host; scripts/, not core). **secret-hygiene PASS.**

---

## (5) ĐÃ CHUẨN — ĐỪNG ĐỤNG (preserve through Phase 4)

1. **Audit hash-chain** (`audit_log_hasher` + `audit_verifier`) — bit-stable SQL↔Python, tamper-evident, per-tenant scoped. SOTA forensic integrity.
2. **PII boundary-redaction, metadata-only persist** (`local_guardrail.py:142-183,:20`) — claude-mem pattern done right; raw PII never touches the log/DB.
3. **IP rate-limit fail-CLOSED + no cap-reveal + trusted-XFF-only** (`ip_rate_limit.py`) — anti-spray can't become DoS amplifier; canonical 2026 shape.
4. **Per-tenant CORS deny-by-default** (`cors_per_tenant.py:219-261`) — fails safe on cache/DB error.
5. **JWT versioned revocation + iss/exp enforce + same-tx outbox cache-drop** (`jwt_token_service.py`).
6. **error_notify_hook fire-and-forget never breaks business logic** + real webhook dispatcher + hot-editable masked notify-channel.
7. **AES-GCM envelope-style decrypt** (`env_secrets.py`) — the KEK/DEK split is correct (only KEK custody should move to KMS, additive).
8. **Anti-abuse + honeypot + security-headers + body-size + source-RL** — the whole edge-defence stack is mature, zero-hardcode, domain-neutral.

---

## (6) OPS-SIDE vs CODE-SIDE SPLIT

**CODE-SIDE (fixable in Phase 4, in-repo):**
- 🐛-KEY: encrypt `api_keys.value_plain` → reserved `value_encrypted` via existing `EnvSecretsAdapter`; backfill migration (HIGH priority).
- 🐛-ALERT: a periodic task calling `cost_cap_alerter.evaluate_tenants` → existing dispatcher (scheduler is the only missing piece).
- 🐛-SLO: a periodic SLO-eval task (p95 vs `DEFAULT_SLO_*` constants, burn-rate) → existing dispatcher. **No new observability infra** (structlog + audit events).
- ↔️-HEALTH: route `health_models` key resolution through `ProviderKeyResolver` so probe mirrors runtime.
- 🐛-CONSENT/EXPORT: consent-record table + withdraw + per-subject export endpoints (D11 + Wave 6 product scope).
- JWT signing-key rotation (kid-based dual-verify) for GA.
- 🐛 (low): replace hardcoded Google-Docs URL in `loadtest_ingest_thongtu_govbot.py` with an env/CLI arg.
- Charter/register edit: cite **PDPL 91/2025 + Decree 356/2025**, not "Nghị định 13".
- (Optional, Port-add) `KmsSecretsAdapter` sibling to `EnvSecretsAdapter` (1 file, registry-swap).

**OPS-SIDE (human/infra, outside repo):**
- WAL/PITR: enable `archive_mode=on` + `archive_command` to off-host bucket (or adopt **pgBackRest** — 2026-std). Reconciles ↔️-DR's RPO-5min claim. **Currently the stated 5-min RPO is unmet — real RPO ≈ 24h.**
- Off-host shipping of pg_dump to cold storage (`backup_db.sh` writes local only).
- Move KEK from `RAGBOT_CONFIG_KEK` env → KMS/HSM (envelope-encryption custody).
- Switch app DSN to `ragbot_app` NOBYPASSRLS (P2-C 🐛 RLS-1) — required before RLS/PITR-restore tenant-isolation invariant holds.
- Cron the cost-cap script (interim, until the in-process scheduler ships).
- Edge WAF / Cloudflare in front of the in-app IP rate-limit (defence-in-depth).
- Periodic **restore drill** ("an untested backup is hope") — DR doc prescribes it; verify it's actually run.

---

## VERDICT

**EVOLVE — do NOT rewrite.** The OPS/SECURITY layer is the most production-mature surface in the program: 22 ✅ components incl. real audit hash-chain, boundary PII-redaction, fail-closed rate-limit, per-tenant CORS, JWT revocation, secret rotation, DR docs+script. **PDPD-ready? PARTIAL** — erasure + audit + PII-boundary are GA-grade, but consent-lifecycle + data-export are absent and the legal target name is stale (Decree 13 → PDPL 2026). **3 heaviest findings:** (1) 🐛-KEY provider keys plaintext at rest in `api_keys.value_plain` [HIGH, code-side, AES-GCM already available]; (2) ↔️-DR WAL/PITR promised but only nightly pg_dump ships → real RPO 24h not 5min [ops-side]; (3) 🐛-ALERT+SLO cost-cap evaluator + latency SLO have **no scheduler/alert** despite all the dispatch plumbing existing [code-side, thin]. **Split:** ~8 code-side fixes (mostly wiring existing parts) + ~7 ops-side infra tasks. D11 maps cleanly: secrets-rotation→🐛-KEY/JWT-signing; backup-DR→↔️-DR/🕰-DR; SLO+alerting→🐛-SLO/ALERT; PDPD→🕰-PDPD + 🐛-CONSENT/EXPORT.
