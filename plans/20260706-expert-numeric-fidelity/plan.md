# [T1-Smartness] Expert root-cause solution — numeric fidelity + full verifiable trace

**Ngày**: 2026-07-06 · Nhánh: `fix-260623-ingest-expert` · Bot tham chiếu: chinh-sach-xe

## Case study (đã đo, evidence-locked)
Bot bịa/vớ giá cho entity ô-giá-trống. Chuỗi gốc rễ (measured):
1. Query "Lốp Neoterra 195/65R16 giá?" → parse keyword có tiền tố "Lốp" → stats
   ILIKE 0 match → fall-through hybrid → LLM nhận **bảng THÔ** (mất cấu trúc cột)
   → vớ giá dòng kề (1.350.000) hoặc bịa (1.500.000).
2. Marker `price: —` đã serve (Fix #1/#2) nhưng **LLM phớt lờ, vẫn bịa 5/5**
   → context-fix = probability-only (constitution P-IV).
3. numeric-fidelity gate BẮT được (row-scoped) nhưng OBSERVE → không chặn.

## Best practice = defense-in-depth 4 lớp (không silver bullet)
| Lớp | Best practice | Trạng thái | Việc |
|---|---|---|---|
| L1 Data→Generate | Serve **record CÓ CẤU TRÚC** (labeled field), không bảng phẳng | narrate có, generate serve raw ❌ | **P1** structure-serve |
| L2 Retrieval | Câu tra-số → route **deterministic** chắc chắn (không LLM đọc text đoán) | stats route có, keyword giòn ❌ | **P2** robust keyword |
| L3 Generation | Chỉ đưa đúng record được hỏi + marker ô trống | marker done ✅ | (xong Fix #1/#2) |
| L4 Verify | Post-check số khớp field + **BLOCK** khi lệch | detector có, observe ❌ | **P3** block net |

Song song: **P4 full verifiable trace** (anh yêu cầu) — tận dụng request_steps +
request_chunk_refs + APP_ENV có sẵn; thêm phần thiếu.

## Phases

### P1 — L2 robust keyword route (đòn bẩy cao nhất, làm TRƯỚC)
Làm price-ask **chắc chắn** vào stats route (nơi serve structured record + marker).
- Root: `parse_price_of_entity_query` trả keyword kèm noise-noun ("Lốp") → ILIKE
  miss. Fix domain-neutral: keyword-match theo **token-subset / spec-token**
  thay vì full-string ILIKE (entity match nếu spec-token của nó ⊆ keyword).
- RED test: keyword "Lốp Neoterra 195/65R16" phải resolve entity "2-R16 195/65 NEO".
- Đo N=10: N-01/N-02 (có "Lốp") route stats → serve `price: —`.

### P2 — L1 structure-serve xuống generate (gốc rễ thật)
Khi retrieve có structured DSI record cho entity, generate nhận **record labeled**
(không raw pipe-row). Best practice TableRAG.
- Khảo sát: generate.py:126 serve `original_content`. Thêm đường: khi chunk là
  stats-synthetic (có structured fields) → giữ labeled form tới LLM.
- RED test + đo: LLM thấy field rõ → giảm vớ-dòng-kề từ nguồn.

### P3 — L4 numeric-fidelity BLOCK (net cuối, owner-approved)
- Per-bot toggle `numeric_fidelity_action` (observe|block), default observe.
- Block khi n_unsupported>0 OR n_misattributed>0 → thay bằng
  `bots.oos_answer_template` (owner text, KHÔNG i18n hardcode).
- FP đã đo: gate 0/84, trap ≤2/69 (chain residual).
- Đo N=10: câu bịa 1.500.000 phải bị chặn.

### P4 — Full verifiable trace (dev/prod gated)
Anh cần: nhận câu hỏi → mấy bước → chunk query → topK → data vào LLM → output
LLM → đủ verify.
- ĐÃ CÓ: request_steps (14+5 bước), request_chunk_refs (chunk qua LLM),
  chat_histories.served_chunks, APP_ENV taxonomy.
- THÊM (phần thiếu):
  * DB: cột `prompt_debug_json` (prompt cuối) + `raw_answer_text` (trước guard)
    trên request_logs — **CHỈ ghi khi APP_ENV=development/uat** (prod=off, tránh
    phình + PII). alembic tracked.
  * File: dev-mode → dump 1 file/request `logs/trace/{request_id}.json` gồm:
    question, steps[], chunk_query, topK, chunks_to_llm[], full_prompt,
    raw_answer, guard_verdict, final_answer.
  * Endpoint đọc: `GET /admin/requests/{id}/trace` (RBAC) để xem trên UI.

## Constitution / sacred check
- P3 block = sacred #10 exception path (owner opt-in + owner template) ✅
- P4 prod-off = PII-lean, dev-only full capture ✅
- Domain-neutral: token-subset match, no "Lốp" literal ✅
- Zero-hardcode: mọi flag qua plan_limits/system_config/APP_ENV ✅

## Thứ tự thực thi
P3 (block, approved, nhanh) → P4 (trace, anh cần verify) → P1 (route) → P2 (structure-serve).
Mỗi phase: RED test → fix → đo N=10 → commit. Không gộp.
