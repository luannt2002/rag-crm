# CONFIG_REFERENCE — nguồn-sự-thật & cách đổi config (chống drift)

> Tại sao có file này: hệ thống có **nhiều nguồn config** (constants code, `system_config` DB,
> `bot_model_bindings`, `bots.plan_limits`, `.env`). Khi đổi nhầm nguồn → thay đổi **no-op âm thầm**
> (vd đổi `system_config.contextual_retrieval_model` nhưng model resolver KHÔNG đọc key đó → vẫn chạy
> model cũ). File này là 1 bản đồ duy nhất: mỗi knob đọc từ đâu, đổi qua đâu, có cần restart không.

---

## 1. Năm nguồn config + thứ tự ưu tiên (precedence)

Resolve theo thứ tự (cao → thấp), lần đầu khớp là dùng:

```
bots.<column>  >  bots.plan_limits JSON  >  system_config (DB, Redis-cached)  >  constants default (code)
```

| Nguồn | File / bảng | Phạm vi | Đổi qua | Hiệu lực |
|---|---|---|---|---|
| **Constants** | `src/ragbot/shared/constants/_NN_*.py` (`DEFAULT_*`) | toàn platform (fallback cuối) | sửa code + deploy | restart |
| **system_config** | bảng `system_config(key,value)` | toàn platform, override constants | **alembic** (CẤM psql) HOẶC admin API | Redis-cached → **bust cache + restart** |
| **bots.plan_limits** | cột JSONB `bots.plan_limits` | per-bot | admin UI / API (audit_log) | per-request (no restart) |
| **bots.\<column\>** | cột thẳng trên `bots` (vd `system_prompt`, `oos_answer_template`, `threshold_overrides`) | per-bot | **alembic** (content) HOẶC admin UI audit | per-request |
| **.env** | `.env` (gitignored) | infra/secret (DSN, API key, base url) | sửa `.env` | restart |
| **Model catalog** | `ai_models`, `ai_providers` | giá + model khả dụng | alembic | restart |

Chi tiết chain per-bot: `src/ragbot/shared/bot_limits.py` (docstring dòng 3).

---

## 2. ⚠️ Model LLM được chọn THẾ NÀO (vùng hay drift nhất)

**Đây là chỗ gây bug "đổi config không ăn".** Model cho mỗi bước KHÔNG đọc từ các key `*_model` rời rạc.
Nó resolve qua **`ModelResolverService.resolve_llm(intent=...)`**:

```
resolve_llm(intent)
  └─ _intent_to_purpose(intent)        # src/ragbot/application/services/model_resolver/__init__.py
        └─ HIỆN TẠI: stub → LUÔN trả "llm_primary"   ← mọi intent dùng cùng 1 model
  └─ lookup bot_model_bindings WHERE purpose='llm_primary' (per-bot)
        └─ fallback: system_config platform default cho purpose đó
        └─ fallback: NullObject
```

### LIVE vs DEAD — bảng sự thật

| system_config key | Có được resolver đọc? | Ghi chú |
|---|---|---|
| `bot_model_bindings.purpose='llm_primary'` | ✅ **LIVE** | nguồn thật cho answer + (hiện cả) enrichment/decompose |
| `default_answer_model` / `llm_default_model` | ✅ một phần | platform default cho `llm_primary` khi không có binding |
| `contextual_retrieval_model` | ❌ **DEAD** | resolver bỏ qua; CR dùng `llm_primary`. Đổi key này = no-op |
| `enrichment_model` | ❌ **DEAD** | như trên |
| `decomposer.model`, `multi_query_model`, `metadata_extraction_model`, `cascade_*_model`, `deepeval_judge_model` | ⚠️ tuỳ bước | một số đọc trực tiếp tại call-site, một số DEAD — kiểm tra call-site trước khi đổi |

> **Nguyên nhân gốc (2026-06-16):** `_intent_to_purpose` là stub trả `llm_primary` cho mọi intent;
> helper `resolve_purpose_for_intent` (cheap-routing factoid/chitchat/oos) ĐÃ viết nhưng **chưa wire**;
> `contextualization`/`enrichment` còn chưa có trong `DEFAULT_CHEAP_INTENT_PURPOSES`
> (`shared/constants/_06_llm_defaults.py`).
>
> **Hệ quả:** muốn enrichment chạy model rẻ (nano) → **KHÔNG đổi `enrichment_model`** (DEAD). Phải:
> (a) wire `_intent_to_purpose` → `resolve_purpose_for_intent`, (b) thêm `contextualization`/`enrichment`
> vào `DEFAULT_CHEAP_INTENT_PURPOSES`, (c) seed `bot_model_bindings`/system_config default cho purpose
> rẻ đó → nano. Xem `reports/CASE_STUDY_INGEST_TPM_COST_20260616.md`.

---

## 3. Các knob hay đụng — bảng tra nhanh

### 3.1 Ingest enrichment (nơi tốn ChatGPT nhất khi upload)
| Key (system_config) | Hiện tại | Ý nghĩa | Restart? |
|---|---|---|---|
| `contextual_retrieval_enabled` | true | bật CR (sinh context/chunk bằng LLM) | có |
| `enrichment_enabled` | true | bật narrate/enrich | có |
| `enrichment_max_concurrency` | 40 | số call enrich song song — **quá cao cho 1 key 200k TPM** | có |
| `contextual_retrieval_max_doc_chars` | 300000 | doc gửi kèm MỖI chunk (~token/​call) | có |
| `contextual_retrieval_prompt_cache_enabled` | true | bật prompt-cache cho doc prefix | có |
| `DEFAULT_CHUNK_CONTEXT_ENRICHMENT_CONCURRENCY` (constant) | 8 | concurrency riêng của CR provider | deploy+restart |

### 3.2 Retrieval / answer
| Key | Ý nghĩa |
|---|---|
| `embedding_model` / `embedding_provider` / `embedding_dimension` | `zembed-1` / `zeroentropy` / 1280 (ZeroEntropy, API riêng) |
| `rag_rerank_top_n` | số chunk sau rerank vào LLM |
| `decomposer.enabled` / `.max_sub_queries` | multi-query decompose (gated theo intent) |
| `chat_worker_concurrency` | worker xử lý chat song song |

> Toàn bộ 261 key: `SELECT key,value FROM system_config ORDER BY key;`

---

## 4. Đổi config thế nào cho ĐÚNG (chống bug đồng bộ)

1. **system_config (DB)** — đổi **CHỈ qua alembic migration** (CẤM `psql UPDATE` thủ công — gây drift,
   không reproduce trên DB khác). Sau khi `alembic upgrade head`: **bust Redis** (`redis-cli FLUSHDB` hoặc
   key cụ thể) **+ restart** service (config cache in-process). Pattern: xem `alembic/versions/*_0222_*`.
2. **bots.system_prompt / oos_answer_template / language_packs** — alembic HOẶC admin UI có audit_log.
   CẤM psql. (sacred rule #7 trong CLAUDE.md)
3. **Constants (code)** — sửa file `_NN_*.py` + deploy + restart. Đây là default cuối cùng.
4. **.env** — secret/infra; sửa file + restart. KHÔNG commit.
5. **Per-bot (plan_limits, columns)** — admin API/UI; hiệu lực ngay (no restart) vì resolve per-request.

**Checklist khi "đổi config mà không ăn":**
- [ ] Key đó có **LIVE** không (mục 2/3)? hay DEAD/đọc ở call-site khác?
- [ ] Đã **bust Redis cache** chưa? (`system_config` cache TTL)
- [ ] Đã **restart** chưa? (config nạp lúc startup / model resolver L1 cache)
- [ ] Có **per-bot override** (binding / plan_limits / column) đè lên platform default không?

---

## 5. Liên kết
- Resolve chain per-bot: [`src/ragbot/shared/bot_limits.py`](../../src/ragbot/shared/bot_limits.py)
- Model resolver: [`src/ragbot/application/services/model_resolver/`](../../src/ragbot/application/services/model_resolver/)
- Constants (24 file): [`src/ragbot/shared/constants/`](../../src/ragbot/shared/constants/)
- Case study TPM/cost ingest: [`reports/CASE_STUDY_INGEST_TPM_COST_20260616.md`](../../reports/CASE_STUDY_INGEST_TPM_COST_20260616.md)
- CLAUDE.md: Zero-hardcode rule · no-psql-hotfix (sacred #7) · Strategy+DI mindset
