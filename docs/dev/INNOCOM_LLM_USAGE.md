# Hướng dẫn sử dụng LLM Innocom + API key

Tài liệu vận hành ngắn — dành cho dev/ops cấu hình Ragbot dùng LM Studio host
của Innocom (server riêng, không phải OpenAI/Anthropic public cloud).

> **Bối cảnh ngắn**: Innocom host LM Studio server tại `https://llm.innocom.co`
> để serve LLM open-source / fine-tuned tại on-prem. Provider tên trong
> Ragbot DB là `lmstudio` (đổi từ `innocom_lmstudio` ở alembic `010v`, để
> tên domain-neutral). Routing prefix LiteLLM giữ nguyên `custom_openai`.

---

## 1. Endpoint + xác thực

| Thông tin | Giá trị |
|---|---|
| **Base URL** | `https://llm.innocom.co` |
| **API path** | OpenAI-compatible (`/v1/chat/completions`, `/v1/embeddings`) |
| **Auth** | `Authorization: Bearer <API_KEY>` |
| **API key format** | `sk-lm-<random>` (LM Studio convention) |

API key liên hệ admin Innocom để xin. **TUYỆT ĐỐI KHÔNG commit key vào git**
(CLAUDE.md sacred rule — `tests/unit/test_no_secret_literal_grep.py` sẽ catch).

---

## 2. Cấu hình `.env` (local dev + production)

Thêm 2 dòng vào `.env` (KHÔNG commit file này — `.env` đã trong `.gitignore`):

```bash
LMSTUDIO_BASE_URL=https://llm.innocom.co
LMSTUDIO_API_KEY=sk-lm-<xin-từ-admin>
```

`.env.example` đã có placeholder 2 dòng này (tracked, không có giá trị thật).

Khi start server, ENV được load qua:
```bash
set -a && source .env && set +a
make dev   # hoặc: uvicorn ragbot.interfaces.http.app:app ...
```

---

## 3. Cấu hình DB — provider + model rows

Provider `lmstudio` đã được seed sẵn qua alembic `010s` + `010v`. Em verify
nhanh:

```bash
PGPASSWORD="$DB_PASSWORD" psql -h <DB_HOST> -U postgres -d ragbot_v2_dev -c "
SELECT name, code, base_url, auth_type, api_key_ref, enabled
FROM ai_providers WHERE name='lmstudio' AND deleted_at IS NULL;
"
```

Kỳ vọng output:

```
   name   |     code      |         base_url          | auth_type |   api_key_ref    | enabled
----------+---------------+---------------------------+-----------+------------------+---------
 lmstudio | custom_openai | https://llm.innocom.co/v1 | api_key   | LMSTUDIO_API_KEY | t
```

Ý nghĩa cột:
- `name` = tên provider (human-readable, dùng admin UI)
- `code` = LiteLLM routing prefix (`custom_openai/<model_id>`)
- `base_url` = endpoint LM Studio
- `auth_type` = `api_key` → header Bearer
- `api_key_ref` = **TÊN ENV VAR** chứa key thật (KHÔNG phải key thật) — runtime
  `EnvSecretsAdapter.resolve("LMSTUDIO_API_KEY")` → đọc `os.environ`
- `enabled` = `t` (true)

---

## 4. Active models on Innocom

Hiện tại 1 model active:

| `name` | `model_id` | `kind` | Context | Use case |
|---|---|---|---|---|
| `gemma-4-e2b-it` | `gemma-4-e2b-it` | `llm` | 8192 | Grading + grounding (legalbot) |

> `qwen3.6-35b-a3b-kimi-k2.6-reasoning-distilled` đã được **soft-delete**
> qua alembic `010u` (2026-05-21). Lý do: reasoning chain forced, latency
> 30-56s/turn, JSON output bị consume hết bởi reasoning. Không phù hợp
> realtime Ragbot. Nếu cần khôi phục test lại: `alembic downgrade 010u`
> → row sống lại (`enabled=true, deleted_at=NULL`).

Verify model list:

```bash
PGPASSWORD="$DB_PASSWORD" psql -h <DB_HOST> -c "
SELECT m.name, m.model_id, m.kind, m.context_window, m.enabled
FROM ai_models m JOIN ai_providers p ON p.id=m.record_provider_id
WHERE p.name='lmstudio' AND m.deleted_at IS NULL;
"
```

---

## 5. Bind model vào bot — `bot_model_bindings`

Để 1 bot dùng model Innocom cho 1 purpose cụ thể, INSERT row vào
`bot_model_bindings`:

```sql
INSERT INTO bot_model_bindings (
    id, record_bot_id, record_model_id, purpose, active, deleted_at
) VALUES (
    gen_random_uuid(),
    (SELECT id FROM bots WHERE bot_id='<bot_slug>' LIMIT 1),
    (SELECT id FROM ai_models WHERE name='gemma-4-e2b-it' LIMIT 1),
    '<purpose>',   -- 'grading' / 'grounding' / 'llm_primary' / 'rerank'
    true,
    NULL
);
```

Purpose hiện hỗ trợ:
- `grading` — CRAG grader
- `grounding` — output guardrail HALLU check
- `llm_primary` — answer generation (KHUYẾN CÁO không dùng Innocom cho
  purpose này — gemma-4-e2b-it không tốt bằng gpt-4.1-mini cho free-form
  answer; verified qua 11-turn UI test 2026-05-21)
- `rerank` — reranker (Innocom hiện không có rerank model)

Verify binding active:

```bash
PGPASSWORD="$DB_PASSWORD" psql -h <DB_HOST> -c "
SELECT bm.purpose, m.name as model, p.name as provider, bm.active
FROM bot_model_bindings bm
JOIN ai_models m ON m.id=bm.record_model_id
JOIN ai_providers p ON p.id=m.record_provider_id
JOIN bots b ON b.id=bm.record_bot_id
WHERE b.bot_id='<bot_slug>' AND bm.active=true AND bm.deleted_at IS NULL;
"
```

---

## 6. Smoke test trực tiếp endpoint

Test endpoint Innocom hoạt động:

```bash
curl -sS https://llm.innocom.co/v1/chat/completions \
  -H "Authorization: Bearer $LMSTUDIO_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemma-4-e2b-it",
    "messages": [{"role":"user","content":"chào em"}],
    "max_tokens": 32
  }' | jq '.choices[0].message.content'
```

Kỳ vọng response Vietnamese greeting trong < 3s.

Nếu lỗi:
- `401 Unauthorized` → API key sai / hết hạn
- `404 model not found` → model chưa load trong LM Studio (liên hệ admin)
- `Connection refused` → server LM Studio down (liên hệ admin)
- `Timeout` → mạng on-prem chậm; tăng `timeout_ms` trong `ai_providers` row

---

## 7. Smoke test qua Ragbot pipeline

Trigger 1 query qua API Ragbot:

```bash
curl -sS https://backendsg.<your-host>:3004/api/ragbot/chat \
  -H "Authorization: Bearer $RAGBOT_JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "bot_id": "<bot_slug>",
    "channel_type": "web",
    "message": "chào em",
    "trace_id": "smoke-test-001"
  }' | jq '.answer'
```

Sau đó verify Innocom đã được gọi:

```bash
PGPASSWORD="$DB_PASSWORD" psql -h <DB_HOST> -c "
SELECT s.step_name, s.metadata_json->>'model_id' as model,
       s.metadata_json->>'provider' as provider
FROM request_steps s
WHERE s.metadata_json->>'model_id'='gemma-4-e2b-it'
ORDER BY s.started_at DESC LIMIT 5;
"
```

Kỳ vọng thấy step `router_select_model` hoặc `grading` / `grounding`
với `model_id=gemma-4-e2b-it`.

---

## 8. Switch endpoint sang LM Studio host khác

Provider `lmstudio` (post `010v`) đã domain-neutral. Để trỏ sang LM Studio
host khác (vd. server staging riêng), KHÔNG cần đổi row DB — chỉ cần đổi
ENV:

```bash
# .env staging
LMSTUDIO_BASE_URL=https://staging-llm.internal:8000
LMSTUDIO_API_KEY=sk-lm-<staging-key>
```

> ⚠️ Nếu cần thay `base_url` cứng trong DB (vd. switch sang Anthropic /
> OpenAI cloud), update qua alembic migration mới — KHÔNG hot edit DB
> trực tiếp. Pattern: `UPDATE ai_providers SET base_url=... WHERE name='lmstudio'`.

---

## 9. Health check + preflight

Ragbot có preflight script kiểm tra provider routing trước khi start:

```bash
python scripts/preflight_routing_check.py
```

Output kỳ vọng:

```
✓ lmstudio: custom_openai/gemma-4-e2b-it (1/1 model resolved)
✓ openai: gpt-4.1-mini (3/3 models resolved)
✓ jina: jina-embeddings-v3 (1/1 model resolved)
```

Nếu fail dòng `lmstudio` → check `.env LMSTUDIO_*` đã export.

---

## 10. Anti-pattern — đừng làm

❌ **KHÔNG commit `LMSTUDIO_API_KEY` thật vào git** — pin test
   `test_no_secret_literal_grep.py` đang scan tracked files; key lộ ra
   khi push origin sẽ bị reject hoặc phải scrub history.

❌ **KHÔNG hardcode `https://llm.innocom.co` trong code Python** — vi phạm
   domain-neutral rule. Endpoint phải từ `ai_providers.base_url` DB.

❌ **KHÔNG dùng `gemma-4-e2b-it` cho `llm_primary`** (free-form answer)
   — quality kém hơn `gpt-4.1-mini` cho RAG answer; verified qua test
   bot `test-spa-id` 2026-05-21. Innocom dùng tốt cho grader + grounding
   (yes/no decision) — không tốt cho narrative answer.

❌ **KHÔNG bypass `EnvSecretsAdapter`** — `api_key_encrypted` column có
   thể dùng để encrypted-at-rest, nhưng hiện workflow là `api_key_ref`
   (ENV var name) → runtime resolve. Đừng inline key vào DB row.

---

## 11. Tham khảo

- Migration history: `alembic/versions/20260521_010[stuv]_*.py`
- Provider resolver: `src/ragbot/infrastructure/llm/router.py`
- Secret resolver: `src/ragbot/infrastructure/secrets/env_secrets_adapter.py`
- Preflight: `scripts/preflight_routing_check.py`
- CLAUDE.md sacred rule: section "Tenant-identifier / secret literals — CẤM HOÀN TOÀN"
