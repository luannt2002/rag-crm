# V2 Embedding Migration — Bug Lessons & Pre-flight Checklist

> **Date**: 2026-05-01
> **Scope**: Jina v3 migration (`text-embedding-3-small` → `jina-embeddings-v3`)
> **Author**: Auditor-Chief (autonomous)
> **Reason**: User explicit "mấy cái này nhớ note lại đấy nhé, để hạn chế bug mấy cái này" + "rerank phải on khi binding active"

---

## 4 Bug Classes Phát Hiện

### BUG #1 — `bot_model_bindings.purpose` naming drift ('rerank' vs 'reranker')

**Symptom**: Live trace báo `rerank mode=null_reranker` dù DB binding row tồn tại + key Jina alive.

**Root cause**:
- `application/services/reranker_resolver.py:57` SQL filter `WHERE b.purpose = 'rerank'`
- Một số binding rows được seed với `purpose='reranker'` (legacy convention)
- SQL miss → `_lookup_db` returns `None` → `_build_from_config(None)` → `NullReranker()`
- Bootstrap path đúng (`build_reranker(provider="jina")` → `JinaReranker`), nhưng query_graph rerank node CALL `reranker_resolver.resolve_for_bot(bot_uuid)` → resolver trả NullReranker → override singleton.

**Fix tạm (DB-only)**:
```sql
UPDATE bot_model_bindings
SET purpose = 'rerank', updated_at = now()
WHERE purpose = 'reranker';
```

**Fix dài hạn**:
1. Alembic migration thêm CHECK constraint:
   ```sql
   ALTER TABLE bot_model_bindings
   ADD CONSTRAINT bot_model_bindings_purpose_chk
   CHECK (purpose IN ('embedding', 'rerank', 'llm_primary', 'grading',
                      'grounding', 'rewriting', 'understand_query', 'decompose'));
   ```
2. Pre-commit grep: `grep -rn "purpose.*'reranker'" src/ scripts/` expect 0 hits
3. Seed scripts: enforce `purpose='rerank'` chuẩn

**Reference files**:
- [`src/ragbot/application/services/reranker_resolver.py:45-66`](../src/ragbot/application/services/reranker_resolver.py)
- [`scripts/db/seed_jina_v3_binding.py`](../scripts/db/seed_jina_v3_binding.py)

---

### BUG #2 — `semantic_cache.py` hardcode 1536-dim column post-V2

**Symptom**: Live trace step `semantic_cache_check` báo `failed: asyncpg.DataError: different vector dimensions 1536 and 1024`. Step fail silent, fall through cache miss.

**Root cause**:
- `semantic_cache._find_similar_impl:295` SQL hardcode column `query_embedding` (1536 dim).
- V2 migration ship `query_embedding_v3 vector(1024)` ở alembic 0054 nhưng `semantic_cache.py` chưa update routing.
- Bot dùng Jina v3 query embedding (1024 dim) → so cosine với column 1536 dim → DataError.

**Hệ quả**: Không phải nguyên nhân top_score thấp (cache miss only skip cache, không break retrieve), nhưng tạo log noise + miss tối ưu hóa cache.

**Fix tạm (skip)**: V2 đã migrate cache TTL ngắn → expire tự động. Có thể defer nếu không có cache hit benefit ngay.

**Fix dài hạn**:
1. Add `embedding_column` kwarg vào `find_similar` / `find_similar_with_text` / `_find_similar_impl` (mirror pattern pgvector_store).
2. Whitelist column name (`embedding`, `query_embedding`, `embedding_v3`, `query_embedding_v3`).
3. Caller (query_graph) pass `state["embedding_column"]` xuống.
4. Test: integration test V2 bot → assert cache write/read dùng `query_embedding_v3` column.

**Reference files**:
- [`src/ragbot/infrastructure/cache/semantic_cache.py:283-336`](../src/ragbot/infrastructure/cache/semantic_cache.py)
- [`alembic/versions/20260501_0054_embedding_v3_parallel_column.py`](../alembic/versions/20260501_0054_embedding_v3_parallel_column.py)

---

### BUG #3 — `DocumentService._embedding_spec` ignore per-bot resolver

**Symptom**: Ingest path luôn dùng OpenAI 1536 dim dù bot binding = Jina v3 1024 dim. Query path dùng Jina v3 đúng. → vector space mismatch trên column 'embedding' (legacy).

**Root cause**:
- `application/services/document_service.py:317-344` `_embedding_spec(self)`:
  ```python
  model_name = await self._cfg.get("embedding_model", self._settings.embedding.model_name)
  ```
  Đọc `system_config.embedding_model` (global, Redis-cached) + `Settings.embedding.*` (.env), KHÔNG nhận `record_bot_id` parameter → không consult `bot_model_bindings`.
- Bot owner update binding to Jina v3, ingest vẫn dùng OpenAI default → mismatch.

**Workaround tạm (đã làm)**: Re-embed 148 chunks tenant 32 trực tiếp qua script với Jina v3 → bypass ingest pipeline.

**Fix dài hạn**:
1. Inject `model_resolver` vào `DocumentService.__init__`.
2. Đổi signature `_embedding_spec(self, *, record_bot_id, record_tenant_id)`.
3. Implementation:
   ```python
   spec = await self._resolver.resolve_embedding(
       record_bot_id=record_bot_id,
       record_tenant_id=record_tenant_id,
   )
   return spec  # đã có task=retrieval.passage default
   ```
4. Update worker job payload: include `record_bot_id` từ document upload context.

**Reference files**:
- [`src/ragbot/application/services/document_service.py:317-344`](../src/ragbot/application/services/document_service.py)
- [`src/ragbot/application/services/model_resolver.py:187-231`](../src/ragbot/application/services/model_resolver.py)

---

### BUG #4 — `_check_reranker_preflight` không recognize jina-prefix model names

**Symptom**: Log `reranker_preflight_unknown_provider note="enabled=true but provider prefix is unrecognised; cannot verify credentials"`.

**Root cause**:
- `interfaces/http/app.py:40` preflight check `model.startswith("jina/") or model.startswith("jina_ai/")`
- DB seed model name = `"jina-reranker-v3"` (không có prefix)
- Preflight không match → log warning + fall through (không raise, không validate)

**Hệ quả**: Misleading log → ops/dev nhầm tưởng provider chưa wired. Không break behavior nhưng làm khó debug.

**Fix dài hạn**:
1. Lookup provider qua `ai_models.record_provider_id` → `ai_providers.code` thay vì guess prefix:
   ```python
   def _check_reranker_preflight(*, enabled, model_name, provider_code=None):
       if not enabled: return
       provider = (provider_code or "").lower()
       if not provider:
           # Fallback to prefix detection (legacy)
           if model_name.startswith("cohere/"): provider = "cohere"
           elif model_name.startswith("jina/") or model_name.startswith("jina_ai/"): provider = "jina"
           ...
       if provider == "jina" and not os.environ.get("RERANKER_JINA_API_KEY"):
           raise RuntimeError(...)
   ```
2. HOẶC enforce naming convention seed time: `{provider_code}/{model_name}`.

**Reference files**:
- [`src/ragbot/interfaces/http/app.py:40-78`](../src/ragbot/interfaces/http/app.py)

---

## Tổng Quát — V2 Migration Mindset

### 5 Quy Tắc Vàng

1. **Parallel column thì PHẢI update mọi nơi đọc column cũ** — không chỉ ORM model. Run `grep -rn "<old_column_name>"` trước commit.

2. **Per-bot resolver > system_config global** — V2 migration phải migrate ALL services dùng config global sang per-bot resolver. Lập checklist trước ship.

3. **Naming convention enum/check constraint** — Postgres CHECK constraint trên `purpose`, `provider_code`, `kind` để fail-fast schema drift.

4. **Silent fallback = anti-pattern khi user expects feature ON** — User explicit 2026-05-01:
   > "api key jina có vấn đề thì thông báo cho tôi đưa key mới, không có làm như thế nhé, pải on nhé"

   KHÔNG fallback NullReranker silent. Fix = raise error hoặc structured warning có severity ERROR + alert + reject ship.

5. **End-to-end smoke after migration** — phải verify TOP_SCORE thực tế > threshold trước claim done. Direct DB cosine không đủ — phải test qua HTTP API.

---

## Pre-Flight V2 Checklist (cho future migrations)

```bash
# 1. Naming convention
grep -rn "purpose.*'reranker'\|purpose.*\"reranker\"" src/ scripts/
# expect: 0 hits

# 2. Hardcoded model name outside constants/settings
grep -rn "DEFAULT_EMBEDDING_MODEL" src/ | grep -v constants.py | grep -v test
# expect: 0 hits in business logic

# 3. Per-bot resolver coverage (no system_config bypass)
grep -rn "system_config.*embedding_model\|settings.embedding.model_name" src/ \
  | grep -v "test\|migration"
# expect: only 1 site = DocumentService (until BUG #3 fixed)

# 4. Vector column coverage
grep -rn "query_embedding\|embedding\b" src/ | grep -v ".pyc\|\.md\|test\|migration"
# Find ALL SQL sites — verify each has column-switch logic

# 5. Preflight provider catalogue
grep -n "model.startswith\|provider_code" src/ragbot/interfaces/http/app.py
# update khi thêm provider mới

# 6. End-to-end smoke (REAL HTTP, không direct DB)
TOKEN=$(curl -s http://localhost:3004/api/ragbot/test/tokens/self \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
RESP=$(curl -s --max-time 60 -X POST http://localhost:3004/api/ragbot/test/chat \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"tenant_id":<TID>,"bot_id":"<BID>","channel_type":"web",
       "connect_id":"smoke","question":"giá triệt lông nách bao nhiêu"}')
echo "$RESP" | python3 -c '
import sys, json
r = json.loads(sys.stdin.read())
top = r.get("top_score", 0)
assert top > 0.30, f"FAIL top_score={top} expected >0.30"
print(f"PASS top_score={top}")
'

# 7. Step trace audit
psql -c "SELECT step_name, status, error FROM request_steps
         WHERE record_request_id='<RID>' AND status='failed'"
# expect: 0 rows

# 8. Reranker actually firing (mode=rerank, not null_reranker)
psql -c "SELECT metadata_json FROM request_steps
         WHERE record_request_id='<RID>' AND step_name='rerank'"
# expect: mode='rerank'
```

---

## Lessons Cụ Thể V2 Session

| Bug | Time spent | Detection method |
|---|---|---|
| #1 purpose='rerank' vs 'reranker' | 3 hours | DB forensic + agent parallel |
| #2 semantic_cache hardcode | discovered first via PARALLEL-3 forensic | request_steps error logs |
| #3 DocumentService bypass | discovered via PARALLEL-2 spec resolve | direct resolver.resolve_embedding() probe |
| #4 preflight prefix mismatch | discovered via log grep | `grep reranker_preflight /var/log/ragbot-api.log` |

**Mức độ tránh được nếu có pre-flight checklist**:
- Bug #1: 100% (CHECK constraint + grep)
- Bug #2: 100% (column-switch grep)
- Bug #3: 100% (per-bot resolver coverage grep)
- Bug #4: 80% (provider catalogue review)

**Time saved next migration**: ~3-4 hours debugging → 30 min checklist execution.

---

## Trace Method Khi Bug Tương Tự

1. **Smoke fail trước**: nếu top_score < expected → STOP, không claim done
2. **Direct DB cosine** (verify embedding chunks đúng task/dim)
3. **Live request trace** qua `request_steps` table → tìm step nào failed/skipped
4. **Bootstrap log** `grep -iE "preflight|fallback|null" /var/log/ragbot-api.log`
5. **Spawn DEBUG agent** với 9 hypothesis matrix khi bug nằm sâu

5 agents song song = ETA 30-40min → giảm xuống ~15-20min.
