# Input-Control là GỐC + 3 triết lý xử lý input (Ragbot vs tldw vs NotebookLM)

> **Loại**: READ-ONLY deep analysis (0 dòng `src/` bị sửa). Mọi claim gắn nhãn SỰ THẬT (file:line/DB) vs GIẢ THUYẾT.
> **Ngày**: 2026-06-25 · **Branch**: `fix-260623-ingest-expert`
> **Cross-link**: [[NOTEBOOKLM_VS_RAGBOT_DEEPDIVE_20260625]] · [[EXTERNAL_NOTEBOOKLM_CLONES_STUDY_20260625]] · design doc `docs/dev/INPUT_DATA_CONTROL_FLOW_DESIGN.md`
> **Plan fix**: `plans/20260625-input-control-silentdrop-multilocale/plan.md`

---

## 0. TL;DR

Gốc của "RAG yếu" **KHÔNG phải answer-flow**, cũng **KHÔNG phải "nhiều format"** — gốc là **CONTROL INPUT ở tầng column-ROLE**: header nào ngoài từ-vựng vi cố định thì **role bị drop IM LẶNG** vào `attributes_json`, owner nhận `success` nhưng cột giá/tồn/alias đã chết. Stress harness cho thấy **nhận diện SHAPE/format đạt 91%** — nên format không phải chỗ vỡ; **column-role + multi-locale** mới là chỗ vỡ. Answer-flow của Ragbot **mạnh hơn open-notebook, ngang tldw** — nó "sai" vì bị đưa input nghèo, không phải vì logic sai.

---

## 1. Bằng chứng thesis "input-control là gốc" = TRUE (design doc tự verify)

`INPUT_DATA_CONTROL_FLOW_DESIGN.md` (đọc trong phiên): *"Verdict: thesis TRUE và load-bearing. Hệ thống KHÔNG crash trên input lạ — nó **degrade IM LẶNG**: markdown (đường LLM/vector) giữ được, nhưng stats-index entity extraction (đường giá/list/superlative deterministic) **mất NGỮ NGHĨA cột** nào header không nằm trong vi-vocab."*

---

## 2. 4 cơ chế silent-drop (file:line)

| # | Cơ chế | Evidence | Hậu quả |
|---|---|---|---|
| 1 | Role-vocab **đóng, exact-match** (lower+NFD, không fuzzy/substring) | `document_stats.py:135-145, 172-179, 307-328` | `Tên hàng`≠`ten` → mất role name |
| 2 | Positional fallback **chỉ cứu name+price** | `document_stats.py:411-425` | cột qty/date/sku **vô hình** với typed query |
| 3 | (ĐÃ FIX MỘT PHẦN) aliases | `document_stats.py:151` `_ALIASES_COL_TOKENS` + alembic `20260624_stats_index_entity_synonyms.py` | aliases **giờ là first-class role** — design doc đã stale ở điểm này |
| 4 | **KHÔNG multi-language** — không `locale`, không `column_role_tokens[locale]` | `document_stats.py` (0 locale awareness — verified grep) | sheet EN/Spanish/Thai → 0 role → fallback name+price |

**Trạng thái THẬT (verified 2026-06-25):** aliases role + entity_synonyms + checker `_unassigned_header_cols` (`check_happy_case.py:86`) **ĐÃ tồn tại**. Gap còn lại thật sự = **#1 exact-match + #4 multi-locale + U5-enrich-skip-cho-bảng**.

---

## 3. Vì sao "happy-case còn sai tới sai lui"

4 file test **KHÔNG phải happy-case** — checker chưa chặn / source chưa sửa:
- xe-3.csv = **synonym-export** (62 cột biến-thể) → 🔴 anti-pattern, phải re-export `Tên|Giá`.
- xe-2.csv = **multi-header vi+CJK+EN manifest** → 🔴 **0 entity**, silent.
- xe-1/2/3 = **3 bảng RỜI mỗi cái 1 thuộc tính** → không gộp WIDE → không dòng nào đủ code+giá+tồn+date.
- Cột **Tồn kho chưa từng ingest** (DB: `chunks chứa 'Tồn' = 0`).

→ "Happy-case sai" = **input không đúng happy-case + checker offline chưa wired vào ingest để báo owner**.

---

## 4. 3 TRIẾT LÝ xử lý input — Ragbot vs tldw vs NotebookLM

| | **Ragbot (anh)** | **tldw_server** | **NotebookLM** |
|---|---|---|---|
| Triết lý | **CONSTRAIN** — 1 IR + checker + normalizer ("fix source first") | **ABSORB** — sở thú parser/OCR | **SIDESTEP** — long-context + closed corpus |
| Multi-format | reject lệch chuẩn về 1 markdown IR | 8 OCR, docling, whisper, trafilatura+Playwright, yt-dlp | Google extraction + nhồi cả nguồn |
| Multi-language | ❌ **vi-only role vocab** (chỗ yếu) | ✅ tokenizer jieba/fugashi/konlpy/pythainlp (ở **chunking**, không ở role) | ✅ Gemini đa ngữ |
| **Có checker/validate?** | ✅ **CÓ — độc nhất** (`check_happy_case.py`) | ❌ KHÔNG — chỉ parse, không reject+báo owner | ❌ KHÔNG |
| **Column-ROLE recognition?** | ✅ **CÓ — mạnh nhất** (name/price/category/aliases) | ❌ **0 hit grep** — chỉ serialize header literal | ❌ KHÔNG (dựa long-context) |
| Dual-rep (markdown + stats-entity) | ✅ **sophistication của anh** | ⚠️ một phần | ❌ |

**Kết luận (quan trọng):**
- **tldw KHÔNG "nói về" input-control kiểu validate/normalize.** Nó không có checker, không có "reject source". Nó chỉ **nhiều parser hơn**. Format dispatch là **if/elif + lazy-import + convention-dict** (`input_sourcing.py:263-356`, `persistence.py:5029-5215`), **không phải registry** — đúng anti-pattern happy-case của anh reject.
- **tldw KHÔNG có column-role** (grep 0 hit) → **không có gì để bê về cho bài toán silent-drop**. `document_stats.py` của anh **đã mạnh hơn tldw**.
- **NotebookLM né** bài toán bằng long-context: input bẩn cỡ nào cũng nhồi vào context. Không cần column-role. Đổi lại trần 50-600 nguồn, không scale multi-tenant.
- **Cái anh CÓ mà cả 2 không có**: checker + dual-representation. Vừa là sophistication, vừa là điểm giòn (stats-index cần role sạch).

---

## 5. Answer-flow KHÔNG phải khâu yếu

- Hybrid dense+BM25+RRF + cliff rerank + CRAG + grounding HALLU=0 **mạnh hơn open-notebook** (chat không retrieve; Ask chỉ cosine full-scan topK=10 không rerank/hybrid) và **ngang tldw**.
- Trong test xe, generate **trung thực** đúng cái được đưa — sai vì **bị đưa 1 chunk nghèo + số không nhãn**.
- Yếu ở query = **stats short-circuit ghim topK=1** (`retrieve.py:537-577`) + **bot không bind reranker** = **dây chưa nối**, không phải answer sai.

---

## 6. 2 thứ worth bê từ tldw (ADAPT, không port code)

| # | tldw source | Verdict | Land ở | Effort |
|---|---|---|---|---|
| 1 | `Chunking/strategies/structure_aware.py:696-752` `_build_contextual_header` breadcrumb `folder>doc>H1>H2` | **ADAPT** | U5 enrich table-row path → bù gap enrich-skip | M |
| 2 | `Chunking/multilingual.py:132-187` `LanguageConfig` map + char-range detector | **ADAPT** | `column_role_tokens[locale]` DB-seed (design §4) | M |
| 3 | `Chunking/multilingual.py:230-297` tokenizer dispatch + import-fallback | ADAPT | `infrastructure/tokenizer/registry.py` (Port đã có) | M/S |
| — | propositions, code_ast, parser-zoo, Table.to_text | **SKIP** | — (English-only/HALLU surface/anti-happy-case/regression) | — |

→ Chi tiết bảng PORT/ADAPT/SKIP đầy đủ trong on-screen analysis + plan.

---

## 7. Kết luận chiến lược

1. **Triết lý happy-case ĐÚNG cho multi-tenant.** Không thể nuôi sở-thú-parser tldw, không thể dùng long-context NotebookLM ở scale 100K docs. Constrain-input là đúng.
2. **Bug = silent-drop, không phải triết lý.** Fix theo design doc anh: SURFACE mọi cột bị drop (hết âm thầm "success") + role-map pluggable theo locale. EVOLVE không REWRITE.
3. **Đừng làm lại cái đã xong** (aliases, entity_synonyms, checker unassigned). Tập trung gap thật: multi-locale role + exact→fuzzy + U5 breadcrumb cho bảng + wire checker-warning vào ingest.

---

*Lập bởi Claude Opus 4.8 (1M context). READ-ONLY. Verified state 2026-06-25.*
