# P2-F · DATA / CACHE / EVENT — Gap audit (Phase 2, STANCE = EVOLVE)

> Re-verified from code, not re-stated from P1-F. Every claim = `file:line` / alembic / commit /
> psql / web-source. Alembic head `0195`. Builds on P1-F-data-cache-event.md + P1-SYNTHESIS §4
> (Q23/Q24) + §44.49 (exactly-once finding). 4 labels: ✅ keep · 🕰 evolve-to-2026-std ·
> ↔️ doc≠code · 🐛 hole.

---

## (1) LABELED COMPONENT TABLE

| Component | Label | Evidence | One-line verdict |
|---|---|---|---|
| **bot_version passive bust** (hash system_prompt+oos) | ✅ | `query_graph.py:974` `_compute_bot_cache_version` = `sha256(system_prompt|oos)[:N]`; stamped into cache key on every read | Prompt edit → key flips → old rows unreachable. Purge-free. **PRAISE.** |
| **corpus_version dual-bump on delete** | ✅ | `corpus_version_service.py:224` `MAX(GREATEST(updated_at, COALESCE(deleted_at, updated_at)))` | A pure soft-delete still flips the marker (deleted_at moves) → no stale-from-deleted-doc. **PRAISE.** |
| **semantic_cache 4-key scoping BEFORE cosine** | ✅ | `semantic_cache.py:419-423` (hash path) + `:479-485` (cosine path): WHERE `record_bot_id AND record_tenant_id AND bot_version AND corpus_version AND expires_at` precedes `<=>` distance | Scope filter runs before similarity; no cross-bot/cross-version leak. **PRAISE.** |
| L1 embedding cache (content-keyed) | ✅ | `embedding_cache.py:22` `ragbot:emb:{model}:{dim}:{sha256(text)}` TTL 30d | Immune to bot/corpus/prompt — correct by construction. |
| provider prompt-cache (Anthropic ephemeral) | ✅ | `anthropic_cache.py:19`, wired `dynamic_litellm_router.py:547/697` | Provider-side, self-invalidates on prompt-prefix change. No app action needed. |
| cache-stampede mutex | ✅ | `semantic_cache.py:231` `SET NX EX` lock keyed `{record_bot_id}:{qhash}` | Standard stampede guard. |
| outbox publisher (per-row SKIP LOCKED + DLQ) | ✅ | `outbox_publisher.py:104-146`, DLQ `:189` after `max_retries=5` | Publisher half is solid (at-least-once + real DLQ). |
| **exactly-once handler delivery** | 🐛 | `redis_streams_bus.py:198-208` dedup `SET NX` BEFORE handler `:215` | **At-most-once on handler failure — message DROPPED.** Worst hole. See §2+§3. |
| **bot soft-delete cascade/purge** | 🐛 | `bot_management_service.py:242-269`: soft_delete + registry-invalidate + outbox only | Purges NOTHING downstream (cache/chunks/corpus_version). See §2. |
| **tenant soft-delete cascade** | 🐛 | `tenant_repository.py` soft_delete; bots FK `ON DELETE RESTRICT` `models.py:130` | Hard-delete structurally blocked; soft-delete orphans everything. See §2. |
| **semantic_cache FK to bots** | 🐛 | alembic `0014:24-40` — `bot_id UUID NOT NULL`, **no FOREIGN KEY** | Even a future hard-delete orphans rows. Root of orphan family. See §2. |
| **stuck-doc reaper (active+0-chunk)** | 🐛 | reaper scans `WHERE state='DRAFT'` only `document_recovery_worker.py:155`; ingest INSERTs `state='active'` `document_service.py:1629` | `active`-with-0-chunk crash window invisible to sweep. See §2+§5. |
| embedding-model-change guard (HOLE-2) | 🐛 | binding swap → no purge, no corpus bump (keyed on doc.updated_at not binding); only no-op counter `query_graph.py:702-727` | Cosine garbage on swap. (→ D10; out of this file's primary scope, flagged.) |
| understand_query_cache per-bot bust (HOLE-1) | 🐛(low) | `understand_query_cache.py:64` keyed on `prompt_version` int, not per-bot system_prompt | Intent cache survives prompt edit. Low blast radius. |
| corpus_version `invalidate()` dead code | 🐛(low) | `corpus_version_service.py:160` defined, **0 callsites** in ingest/delete | 300s TTL lag instead of event-driven bump. |
| consumer-side DLQ | 🕰 | `redis_streams_bus.py:348-359` = log + XACK at `times_delivered>5`, no persistence | Asymmetric vs publisher DLQ. See §4. |
| per-tenant ingest fairness | 🕰 | one global stream `redis_streams_bus.py:60` + shared `Semaphore(5)` `:170` | Noisy-neighbor (→ D8). |
| `build_response_cache_key` | ↔️ | `cache_port.py:103` scoped-correct but **0 callsites** | Dead canonical builder. See §6 note. |
| domain `Document` DRAFT state-machine | ↔️ | `document.py:31/199` DRAFT→PUBLISHED/ARCHIVED; real ingest uses `active`/`failed` `document_service.py:1629/3682` | Two divergent state vocabularies. See §5. |

**Counts:** ✅ = 7 · 🐛 = 9 (3 low-sev) · 🕰 = 2 · ↔️ = 2.

---

## (2) 🐛 EACH HOLE + REPRO SKETCH

### 🐛 H-EO — Exactly-once = at-most-once on handler failure (WORST)
Trace (re-read line-by-line):
1. `_dispatch_one` decodes payload, then `SET NX EX DEFAULT_OUTBOX_DEDUP_TTL_S` on
   `ragbot:outbox:dedup:{msg_id}` → `was_new=True` first time (`redis_streams_bus.py:198-201`).
2. `await handler(_Event(payload))` (`:215`). **If handler raises → except `:217` logs → NO XACK.**
3. Message stays in PEL. After 30s idle, `recover_pending_messages` XCLAIMs it (`:323-377`, called
   every ~60s `:256`).
4. Redelivery re-enters `_dispatch_one` → `SET NX` returns `was_new=False` (key still set, TTL
   86400s `_07_llm_sampling_defaults.py:114`) → `:202-208` log `dedup_skip` + **`xack` + return**.
   **Handler never re-runs. Message silently dropped.**

**Repro test sketch** (`tests/integration/test_eventbus_exactly_once.py`, do NOT commit):
```
1. fakeredis + RedisStreamsEventBus. publish_raw(subject, payload, msg_id="row-uuid-1").
2. subscribe(subject, handler=raise_once_then_succeed)  # 1st call raises, 2nd would succeed.
3. await first delivery → handler raised, assert NO xack (XPENDING shows 1 entry).
4. force recover_pending_messages(min_idle_ms=0) → redelivery fires.
5. ASSERT handler.call_count == 1  (proves 2nd dispatch was dedup-skipped, never re-ran)
6. ASSERT XPENDING == 0 (proves it was XACKed = permanently dropped).
   → test currently PASSES the bug (asserts the drop). Inverts to handler.call_count==2 post-fix.
```

### 🐛 H-BOT — Bot soft-delete purges nothing downstream
`delete_bot` (`bot_management_service.py:242-269`) = `soft_delete` (`is_deleted=true`) +
registry-invalidate + audit + outbox `registry_changed`. Semantic_cache rows orphan (no FK,
alembic `0014`), corpus_version Redis key lingers TTL 300s, conversations/messages/documents/chunks
stay (FK CASCADE on `bots.id` fires only on **hard** delete, which never happens). Storage grows
unbounded; no read-time leak only because UUID rotates.

**Repro sketch** (`test_bot_lifecycle_purge.py`): seed bot + 1 semantic_cache row + 3 chunks →
`delete_bot()` → assert `SELECT count(*) FROM semantic_cache WHERE record_bot_id=:b` == 0 (FAILS:
row survives), assert corpus_version Redis key deleted (FAILS), assert chunks purged (FAILS).

### 🐛 H-TEN — Tenant soft-delete cascade absent + RESTRICT blocks hard delete
`bots → tenants` FK = `ON DELETE RESTRICT` (`models.py:130`) → a hard tenant delete is
structurally impossible while any bot exists; soft-delete (`tenant_repository.py`) leaves all child
data + cache live, no orchestrated cascade. Plan `260608-multitenant-hardening/plan.md:72` already
flags "Deletion cascade → verify 0 orphan" as OPEN — confirms not implemented.

**Repro sketch**: seed tenant + 2 bots + N chunks/cache → `soft_delete_tenant()` → assert orphan
count across {chunks, semantic_cache, corpus_version keys, registry keys} == 0 (all FAIL today).

### 🐛 H-FK — semantic_cache has no FK to bots
alembic `0014:24-40` declares `bot_id UUID NOT NULL` with **no `REFERENCES bots(id)`**. Means even
when H-BOT is fixed with a hard delete, rows orphan unless app-purge runs first. Add
`FK … ON DELETE CASCADE` so DB self-cleans; trade-off = pgvector/HNSW index interaction on cascade
delete (acceptable — cascade is rare admin path, not hot path).

**Repro sketch**: `test_semantic_cache_fk.py` — `\d+ semantic_cache` / `information_schema.
table_constraints` query → assert a `FOREIGN KEY` to `bots` exists (FAILS today).

### 🐛 H-REAP — Stuck `active`-with-0-chunk reaper missing
The UPSERT ingest path INSERTs `state='active'` **synchronously** (`document_service.py:1629`)
BEFORE the async chunk+embed. The terminal flip to `active`/`failed` happens only after counting
chunks (`document_service.py:3682-3699`). A worker crash between 1629 and 3682 → row stuck
`state='active' AND chunk_count=0`. The reaper scans **only** `WHERE state='DRAFT'`
(`document_recovery_worker.py:155`) → these rows are invisible to it. (Domain entity's DRAFT machine
at `document.py:199` is a *different, unused* vocabulary — see ↔️ §5.)

**Repro sketch** (`test_reaper_active_zero_chunk.py`): INSERT doc `state='active'`, 0 chunks,
`created_at = now()-1h` → run `_scan_stuck_documents()` → assert it returns the row (FAILS:
predicate is `state='DRAFT'`). Post-fix predicate `state IN ('DRAFT') OR (state='active' AND
chunk_count=0 AND created_at < now()-threshold)`.

---

## (3) EXACTLY-ONCE RE-VERIFICATION VERDICT

**CONFIRMED — refute of "saved by retry/DLQ" is itself refuted; the claim holds: NOT exactly-once,
degrades to at-most-once on handler failure.**

I re-read the entire subscribe/dispatch/ack/dedup/recover/DLQ chain. The XCLAIM retry path
(`recover_pending_messages:323-377`) and the consumer "DLQ" (`:348-359`) do **NOT** save the
message — they make it *worse*:
- XCLAIM **does** redeliver (good intent), but redelivery hits the already-set dedup key
  (`SET NX` from the first, failed attempt; TTL 86400s) → `was_new=False` → `dedup_skip` →
  **XACK + return** (`:202-208`). The redelivery the recovery loop worked to produce is
  immediately discarded.
- The consumer "DLQ" only fires at `times_delivered>5` (`:348`) and is **log + XACK**, no
  persistence, no replay queue. But the dedup-skip XACKs the message on the **2nd** delivery, so it
  never reaches delivery count 5 — the DLQ branch is dead for this failure mode.
- There **is** a partial saver: `document_recovery_worker` re-emits a *fresh* outbox row (new UUID
  → new dedup key) for `state='DRAFT'` docs older than threshold. But (a) it only covers the
  `document.uploaded.v1` subject, and (b) per H-REAP it scans the wrong state (`DRAFT`), so the
  real `active`+0-chunk crash window is uncovered. Other subjects (registry_changed, feedback) have
  no equivalent sweeper.

**Verdict: the at-most-once claim is TRUE.** Root cause = dedup-mark placed *before* handler
success, in a store (Redis) separate from the handler's side-effects (Postgres) — the classic
"mark-before-dispatch" anti-pattern (§4). One transient handler exception permanently consumes the
only delivery.

---

## (4) 🕰 WHAT IS THE 2026 STANDARD

### Outbox + dedup-before-handler  vs  transactional-outbox + idempotent-consumer
**2026 standard = process-then-mark (idempotent consumer / inbox), dedup AFTER success, atomic with
side-effects.** The literature is explicit that Ragbot's ordering is a named anti-pattern:

> "A critical anti-pattern emerges when marking messages as processed **before** handler dispatch.
> If a worker crashes after marking … but before processing, the message won't be redelivered …
> **causing message loss.**" — Architecture-Weekly / OneUptime (2026).

The correct shape: consumer "upserts a `processed_events` marker **after successful apply**; on
restart, duplicates become no-ops" — the dedup write lives in the **same DB transaction** as the
handler's state change (the **Inbox** table), so there is no second non-atomic system. At-least-once
delivery × idempotent apply = effective exactly-once; loss is impossible because the marker and the
work commit or roll back together. Kafka's own EOS commits offsets **only after** successful
processing for the same reason.

**Mapping to Ragbot:** move the dedup mark from Redis-`SET NX`-before-`handler` to a Postgres
`inbox(msg_id PK, processed_at)` row inserted **inside the handler's own tx** on success; XACK after
that commit. Keep the Redis `SET NX` only as a cheap fast-path *optimisation* (skip obviously-seen),
never as the source of truth. This is a WIRE/HARDEN change inside the existing event-bus Port — no
rewrite.

### Redis Streams  vs  a "claim ledger" design
Redis Streams + consumer groups (XREADGROUP/PEL/XCLAIM) is a **legitimate 2026 choice** for
at-least-once; antirez's own consumer-group patterns and Redis' idempotency doc endorse
"stream-entry-id as dedup key with TTL" — but **only when the dedup write is idempotent-after-apply,
not before**. Ragbot already has the right primitives (PEL, XCLAIM crash-recovery, NOGROUP
self-heal); it is the *ordering* that is wrong, not the broker. No need to swap Redis Streams for a
heavier claim-ledger/Kafka-EOS stack — fixing the marker placement + adding the inbox table closes
the gap within the current engine (consistent with EVOLVE).

**Consumer-side DLQ (🕰):** SOTA persists poison messages to a replayable parking-lot stream;
Ragbot's `log + XACK` (`:348-359`) loses them. Add a `ragbot:{subject}:dlq` stream + admin replay.

Sources:
[Architecture-Weekly — Deduplication in Distributed Systems](https://www.architecture-weekly.com/p/deduplication-in-distributed-systems) ·
[OneUptime — Exactly-Once with Redis Streams (2026)](https://oneuptime.com/blog/post/2026-03-31-redis-exactly-once-processing-streams/view) ·
[Milan Jovanović — Inbox Pattern](https://www.milanjovanovic.tech/blog/implementing-the-inbox-pattern-for-reliable-message-consumption) ·
[event-driven.io — Outbox/Inbox & delivery guarantees](https://event-driven.io/en/outbox_inbox_patterns_and_delivery_guarantees_explained/) ·
[microservices.io — Transactional outbox](https://microservices.io/patterns/data/transactional-outbox.html) ·
[Redis docs — Idempotent message processing](https://redis.io/docs/latest/develop/data-types/streams/idempotency/)

---

## (5) Q23 BotLifecycleService + Q24 stuck-doc reaper — DESIGN

### Q23 — `BotLifecycleService.purge(record_bot_id, record_tenant_id)`
Orchestrated cascade (saga), idempotent, audit-logged. **What it MUST purge** (each item =
verified hole above):

| # | Target | Why / evidence | How |
|---|---|---|---|
| 1 | `document_chunks` (+ `request_chunk_refs` via CASCADE) | survive soft-delete (H-BOT) | `DELETE FROM document_chunks WHERE record_bot_id` (CASCADE handles refs) |
| 2 | `semantic_cache` rows | no FK (alembic `0014`) → never auto-cleaned (H-FK/H-BOT) | `DELETE FROM semantic_cache WHERE record_bot_id AND record_tenant_id` |
| 3 | bot registry Redis key | already done on delete `bot_management_service.py:256` — **keep** | `registry.invalidate(4-key)` |
| 4 | understand_query Redis cache | HOLE-1, keyed `ragbot:uq:v*:{record_bot_id}:*` | `SCAN + UNLINK` by `record_bot_id` prefix |
| 5 | corpus_version Redis key | HOLE-4, `invalidate()` exists but dead `corpus_version_service.py:160` | **call the existing `invalidate(record_tenant_id, record_bot_id)`** |
| 6 | embedding cache (Redis L1) | content-keyed, shared across bots → **MUST NOT purge** (would evict other bots) | no-op (document the deliberate skip) |
| 7 | dedup ledger (`ragbot:outbox:dedup:*`) | keyed by outbox-UUID not bot → **not bot-scoped**; let TTL expire | no-op (document) |
| 8 | `documents` / `conversations` / `messages` | survive soft-delete; FK CASCADE only on hard delete | soft-delete cascade OR final hard `DELETE FROM bots` after 1-7 (then FK CASCADE fires for free) |

**Ordering:** purge children (1,2,4,5) first → then registry invalidate (3) → then optional hard
`DELETE FROM bots` so the FK CASCADE (`documents/conversations/chunks` on `bots.id`) does items 1/8
atomically. Run inside `session_with_tenant` (RLS-scoped), emit one `bot.purged.v1` outbox event +
audit row. **Tenant purge = fan-out** of `BotLifecycleService.purge` over all bots, then tenant row
(requires lifting FK `ON DELETE RESTRICT`→guarded-cascade, Q10/H-TEN). Idempotent: every step is
`DELETE … WHERE` (re-run = 0 rows).

### Q24 — Stuck-document reaper (extend existing sweeper, don't add a 2nd worker)
Today: `document_recovery_worker._scan_stuck_documents` scans `state='DRAFT'`
(`document_recovery_worker.py:155`). **Extend the predicate** to also catch the real prod crash
window (H-REAP):
```sql
WHERE d.deleted_at IS NULL
  AND d.created_at < now() - make_interval(secs => :stuck_threshold_s)
  AND (
        d.state = 'DRAFT'
     OR (d.state = 'active' AND d.chunks_processed = 0)   -- worker crashed after INSERT, before flip
      )
  AND o.id IS NULL   -- keep the anti-duplicate LEFT JOIN
```
- `chunks_processed` column exists since alembic `0093` — usable directly (or `NOT EXISTS
  (SELECT 1 FROM document_chunks WHERE record_document_id=d.id)` for a stricter check).
- Reuse the existing replay path (`_replay_one_document`) — re-emit `document.uploaded.v1`; the
  ingest worker re-runs ingest exactly as a re-upload (`force_reingest=False` is fine, content_hash
  skip-embed still guards).
- **Better long-term (recommended in ADR):** make ingest write `state='DRAFT'` on first INSERT
  (matching the domain entity `document.py:199`) and flip to `active` only at `:3682`. Then the
  *existing* DRAFT-only sweeper already covers the window with zero predicate change, and the two
  divergent state vocabularies (↔️ §1) collapse into one. Trade-off: a transient window where a
  freshly-uploaded doc is queryable-as-DRAFT=false — acceptable, and arguably correct (don't serve
  un-embedded docs).
- Cadence/threshold already config-driven (`DEFAULT_RECOVERY_INTERVAL_S=300`,
  `DEFAULT_RECOVERY_STUCK_THRESHOLD_S`) — no new hardcode.

---

## (6) ĐÃ CHUẨN — ĐỪNG ĐỤNG (keep, evidence-backed)

1. **bot_version + corpus_version passive bust** (`query_graph.py:974`,
   `corpus_version_service.py:224`). Version-stamped keys = the 2026-recommended invalidation over
   explicit purge fan-out. A prompt edit or a doc delete makes old rows *unreachable* at read with
   zero purge. The dual-bump `GREATEST(updated_at, deleted_at)` is the subtle correct bit — a pure
   delete still flips the marker. **Touching this re-introduces stale-answer bugs.**
2. **semantic_cache 4-key scoping precedes cosine** (`semantic_cache.py:419-423` / `479-485`).
   Scope WHERE runs before `<=>` distance → structurally impossible to return another bot's/version's
   cached answer. Keep the scope-before-similarity ordering.
3. **Outbox publisher half** (`outbox_publisher.py:104-146/189`): per-row `FOR UPDATE SKIP LOCKED`
   + XADD-durability `BusError` (`redis_streams_bus.py:139-140`, commit `e467e1e`) + real DLQ +
   forensic `redis_entry_id`. A Redis blip cannot mark a row processed (`e467e1e`). This half is
   already at-least-once-correct — **the fix in §3 is consumer-side only; do not touch the
   publisher.**
4. **NOGROUP self-heal + XCLAIM crash recovery** (`redis_streams_bus.py:222-248/323-377`, commit
   `1fd50c8`). The recovery *machinery* is correct; only the dedup-marker *placement* feeding it is
   wrong. Keep the recovery loop; move the marker (§4).
5. **Content-keyed embedding cache + provider prompt-cache** — correct by construction, no app
   invalidation needed. The §5-Q23 design deliberately does NOT purge embedding cache (shared).

> Note on ↔️ `build_response_cache_key` (`cache_port.py:103`, 0 callsites): scoped-correct but dead.
> Either adopt as the canonical key builder or delete — do not leave as a misleading duplicate. Not
> a hole (nothing reads it), flagged for hygiene only.
