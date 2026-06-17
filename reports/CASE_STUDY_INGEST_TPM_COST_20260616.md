# Case study — ingest enrichment làm vỡ TPM 200k (chi phí & throttle)

**Ngày:** 2026-06-16 · **Trigger:** reinit re-ingest 9 doc cùng lúc → 3 bot, legal/xe fail (DRAFT, 0 chunk).

---

## 1. Triệu chứng
- `RAGBOT-ALERT: LLMError @ ingest.pipeline` — `litellm.RateLimitError: ... TPM Limit 200000, Used 200000`.
- spa ingest OK (134 chunk), **xe + legal fail** → DRAFT 0 chunk. Recovery/retry → **retry storm** giữ TPM luôn cạn.

## 2. Nguyên nhân trực tiếp (evidence)
- Mỗi call CR enrichment **~25.000 token** (log `Requested 25297`) vì template gửi **full doc** (`Document: {doc}`,
  `contextual_retrieval_max_doc_chars=300000`) kèm MỖI chunk.
- `enrichment_max_concurrency=40` + CR provider `gather(tất cả chunk)` **cold đồng thời** → ~40×25k = **~1M token/giây** vs trần **200k** → 429 hàng loạt.
- 1 key OpenAI duy nhất (`.env OPENAI_API_KEY`); `api_keys` pool có 0 key openai (3 key ZeroEntropy, API riêng). → toàn bộ ChatGPT chia 200k TPM của 1 org.

## 3. Gốc rễ (chain)
1. **Cold fan-out**: `llm_chunk_context_provider.generate()` gather toàn bộ chunk cùng lúc → N bản full-doc gửi song song, prompt-cache (đã bật) **không kịp warm** (mọi call cold cùng lúc → cache miss, `cached_tokens=0`).
2. **Concurrency/doc-size không tương thích TPM**: 40 concurrency × 25k token/call ≫ 200k TPM.
3. **Model resolver bỏ qua config model**: `_intent_to_purpose()` là **stub trả `llm_primary`** → CR/enrichment chạy `gpt-4.1-mini` (đắt) dù `system_config.contextual_retrieval_model` / `enrichment_model` đã set nano → **2 key đó DEAD**. (Xem `docs/dev/CONFIG_REFERENCE.md` §2.)
4. **Thiếu throttle chủ động + backoff honor Retry-After** → retry dồn = thundering herd.
5. **Degrade chưa phủ hết**: CR chunk-context + narrate đã degrade (return ""/raw), nhưng vẫn còn call ingest fail làm chết cả doc.

## 4. Expert solution (best-practice, fast + cost-controlled)

| Lớp | Giải pháp | Trạng thái |
|---|---|---|
| **① Prompt-cache warm-then-fan-out** | Gọi chunk #1 một mình → warm cache doc prefix → fan-out phần còn lại đọc cache. `N×doc` → `1×doc + N×chunk` (~10×). Đây là cách Anthropic Contextual-Retrieval chính thức làm. | ✅ ĐÃ code `llm_chunk_context_provider.generate()` (32 test pass). ⚠️ chưa chứng minh cache-hit (warm-call 429 khi TPM nghẽn → cần ② + ③). |
| **② Token-bucket TPM limiter** | Đo requested-tokens/phút, vượt thì **xếp hàng** không bắn. litellm Router hỗ trợ `tpm`/`rpm`+queue. `concurrency = TPM / token_mỗi_call`, KHÔNG hardcode 40. | ❌ TODO |
| **③ Backoff honor Retry-After** | 429 báo "retry in 7.5s" → đợi đúng + exponential. Warm-call phải retry tới khi thành công để seed cache. | ❌ TODO |
| **④ Degrade triệt để** | Mọi call enrichment 429 (sau backoff) → embed raw chunk, KHÔNG fail doc. | ◑ một phần |
| **⑤ Model rẻ cho enrich** | `gpt-4.1-nano` ($0.16/$0.64) thay mini ($0.40/$1.60) cho CR+narrate+decompose (task extractive). Giữ mini cho answer/grounding/grade. | ⚠️ cần wire resolver (đòn 3 ở §3) — đổi config thôi KHÔNG ăn |
| **⑥ Vận hành** | Nâng OpenAI tier (TPM cao hơn) · multi-key/multi-org pool (`api_keys` đã hỗ trợ, mở cho openai như ZE) · Batch API cho enrichment (rẻ 50%, pool riêng) · fallback Azure | tuỳ chọn |

**Combo tối thiểu đủ prod:** ① + ② + ③ + ④ (+ ⑤ khi đã wire resolver). Kết quả: upload nhiều → ingest **chậm lại có kiểm soát**, token giảm ~10–25×, KHÔNG 429, KHÔNG mất data.

## 5. Bài học (đưa vào quy trình)
- **Chunking KHÔNG cần LLM** (CPU thuần). Chi phí ChatGPT khi upload = **enrichment phụ trợ** (CR + narrate), config-gated. Embedding = ZeroEntropy (API + limit riêng).
- **Concurrency phải suy ra từ TPM**, không phải hằng số tuỳ ý.
- **Đổi config model phải check LIVE/DEAD** trước (resolver stub) — xem `CONFIG_REFERENCE.md`.
- Workaround tạm thời cho demo: ingest **từng bot một** (có TPM headroom) thì lọt — KHÔNG phải fix gốc.

## 6. Trạng thái khi viết
spa 134 ✓ · xe 486 ✓ · **legal 0 ✗** (chờ ②③ hoặc hạ concurrency + giãn). warm-fanout (①) đã ship code; nano-config đã set nhưng DEAD tới khi wire resolver.
