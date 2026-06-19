# Clean rebuild + 5-criteria Expert-RAG validation

**Goal:** wipe dev DB + vector + cache → re-init 3 bots + sysprompts (governed) →
upload 9 files → load-test ALL flows → score vs 5 criteria (Fast · Faithful=100% ·
UX · Performance · Cost) → gap list.

**Safety:** pg_dump backup taken → `/tmp/ragbot_backups/ragbot_pre_rebuild_20260619_144837.dump`
(restore: `pg_restore -d "$PSQL_URL" --clean --if-exists <dump>`).

**Verified state (2026-06-19):** API up (health 200) · Redis `localhost:6380/0` ·
9 docs/1346 chunks/3 bots now · all scripts+scenarios present · pg_dump 16.14.

**Honest framing (rule #0):** this cycle = clean rebuild + **measure VERIFIED
baseline against the 5 criteria + produce the gap list**. It is NOT a one-shot
"achieve 100%". Hitting all 5 (esp. Faithful=100% + low-latency + low-cost
together) is iterative: measure → fix gap → re-measure.

---

## Phase A — wipe + re-init (DESTRUCTIVE · GATED · sequential, I drive)
```bash
set -a && source .env && set +a
# 1. DROP + recreate schema + extensions (irreversible — backup above)
python -c "import os,asyncio,asyncpg
async def m():
 u=os.environ['DATABASE_URL_SYNC'].replace('postgresql+psycopg2://','postgresql://')
 c=await asyncpg.connect(u)
 await c.execute('DROP SCHEMA IF EXISTS public CASCADE'); await c.execute('CREATE SCHEMA public')
 await c.execute('GRANT ALL ON SCHEMA public TO ragbot, public')
 await c.execute('CREATE EXTENSION IF NOT EXISTS vector'); await c.execute('CREATE EXTENSION IF NOT EXISTS pgcrypto')
 await c.close()
asyncio.run(m())"
# 2. schema + post-squash migrations (RE-PROVISIONS ragbot_app + ragbot_system idempotently)
.venv/bin/alembic upgrade head
# 3. seed: system_config + RBAC + language_packs + 3 bots + sysprompts + providers/models + quota
.venv/bin/python scripts/db/seed_dev.py
# 4. flush Redis (cache + semantic vectors live partly here)
redis-cli -p 6380 -n 0 FLUSHDB
# 5. restart API so it picks up fresh schema/seed (kills stale pooled conns)
#    (find current process; relaunch uvicorn :3004; wait health 200)
```
**Verify A:** 3 bots present w/ system_prompt non-null; alembic head =
`rls_system_role_grants_20260619`; health 200.

## Phase B — upload 9 files (external deps: Google fetch + Jina embed · sequential)
```bash
.venv/bin/python scripts/init_bots_from_urls.py --wipe --apply   # 9 URLs -> 3 bots, polls ~57s
```
**Verify B (psql):** all 9 docs `state=active`, `chunks_stored>0`, `null_embed=0`,
zero stuck DRAFT.

## Phase C — load-test ALL flows (parallelizable — 3 bots / 3 harnesses)
- `eval_gate.py --coverage-floor 0.85` — deterministic HALLU=0 gate + coverage + p95 (3 bots).
- `loadtest_qa_detail.py --stamp rebuild` — per-turn evidence + fail_step attribution (42Q across 3 bots).
- `loadtest_graded.py test-spa-id chinh-sach-xe thong-tu-09-2020-tt-nhnn` — LLM-judge L0–L5, fabricate detection (needs OPENAI_API_KEY, RUNS=3).
- 18-flow inventory (F01–F18): factoid, structural-anchor, aggregation, superlative/filter,
  comparison, multi-hop, OOS-refusal, HALLU-trap, booking, greeting, semantic-cache,
  prompt-cache, guardrail, coreference, typo, abbreviation, cross-ref, rewrite/rerank.

## Phase D — score vs 5 criteria + gaps (parallel scoring → synthesis)
| Criterion | Metric | Source |
|---|---|---|
| Fast | p50/p95/p99, TTFT(SSE) | test_75q/eval_gate; streaming_smoke |
| Faithful=100% | HALLU=0 sacred (6 traps) + coverage + graded fabricate | eval_gate, graded |
| UX | refuse_rate, REFUSE_GAP, must_cite | eval_gate, golden |
| Performance | pass_rate, top_score, fail_step | qa_detail, graded |
| Cost | cost_usd/turn, tokens, cache_hit% | test_75q, qa_detail |

**Known measurement GAPS (from discovery):** TTFT not in non-stream harness ·
HALLU sub-types misinterpret/extrapolate no counter · rerank+embed cost discarded
(not logged) · LLM-call-count not aggregated · RAGAS faithfulness = stub ·
missing fixtures `LUANNT_LOAD_TEST_75Q.md`/`CODER_LOADTEST_90Q_FIXTURE.json` (skip those harnesses).

**Governance flags (pre-existing, NOT blocking — log for cleanup):** `seed_dev.py`
raw-writes `system_config.value` + `language_packs.content` (sacred per CLAUDE.md
rule 7) — it's the tracked dev-seed path so reproducible, but should move into alembic.

## Rollback
Any failure in A/B → `pg_restore --clean --if-exists` the backup; restart API.
