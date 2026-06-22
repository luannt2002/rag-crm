# [T1/T3] CONTROL PLAN — fix-register cho mọi item còn lại (upload + query flow)

> Tier: hỗn hợp (T1-Smartness cho answer-quality, T3-Refactor cho compliance/cleanup).
> Ngày: 2026-06-22 · Branch: `expert-rag-squash-conflate-logcenter-20260619`.
> Mindset (CLAUDE.md): rule #0 no-guess · **TDD bắt buộc** (test trước, verify sau) ·
> surgical · domain-neutral · ship từng cái (1 item = 1 commit) · EVOLVE-not-rewrite.

---

## 0. Bối cảnh

UPLOAD (L1→L7) + QUERY (Q1→Q8) đã verify **HEALTHY** (load-test 96%, 2 debugger ALL GREEN).
Còn lại = **POLISH** (không vỡ main flow): 6 bug từ multi-agent audit (adversarial-verified) +
2 minor từ query deep-debug. Plan này **CONTROL** chúng: 1 register, priority, TDD, status.

**Nguyên tắc**: main flow đã đúng → các item này là **cải thiện/compliance**, KHÔNG khẩn cấp.
Ship từng cái có TDD; verify load-test KHÔNG tụt 22/23 sau mỗi fix.

---

## 1. CONTROL REGISTER (mọi item — id, sev, vị trí, tác động, fix, status)

| ID | Sev | Flow | File:line | Vấn đề | Fix approach | Prio | Status |
|----|-----|------|-----------|--------|--------------|------|--------|
| **A** | WARN-domain | L7 narrate | `llm_narrate.py:58-72` | prompt narrate hardcode tiếng Việt | chuyển prompt sang `system_config`/`language_packs` (per-locale), default domain-neutral | **P1** | TODO |
| **B** | WARN-domain | Q7 generate | `generate.py` | ~~`price_buoi_le`/`price_goc`~~ → generic | đổi key generic + `conversation_state.py` back-compat | **P1** | ✅ DONE |
| **C** | WARN | Q7 generate | `generate.py` | ~~CSV-only extract~~ → delimiter-aware (`,` + `\|`) | helper `_extract_locked_prices` markdown+CSV, test 5/5 | **P2** | ✅ DONE |
| **D** | BLOCKER | Q6/L7 | `ingest_stages_store.py:659` | parent chunks thiếu narrate metadata | narrate parent trước store HOẶC Q6-expand dùng leaf-narrate | **P2** | TODO |
| **E** | BLOCKER | L2 upload | `analyze.py:196` | ~~regex match prose-1-pipe~~ → yêu cầu ≥2 pipe | `_is_table_line` header-branch + `count('\|')>=2`, test 4/4 | **P3** | ✅ DONE |
| **F** | WARN | L2 upload | `blocks.py:257-265` | markdown heading không tag riêng (gộp text) | thêm branch `# ## ###` → block-type `heading` (hoặc giữ — HDT đã xử ở L4/L6) | **P3** | TODO |
| **G** | MINOR | Q1 understand | intent prompt | superlative "đắt nhất" → `factoid` (đáng `aggregation`); rewrite rỗng | thêm ví dụ superlative vào intent prompt; rewrite fallback = query gốc (đã có) | **P4** | TODO |
| **H** | MINOR | Q3/Q4 retrieve | retrieval/BM25 | xe tire-size "165/80R13" miss (notation cross-match) | BM25 AND + notation normalize; cần ADR (đổi retrieval) | **P4** | DEFER-ADR |

---

## 2. PRIORITY — lý do xếp tầng

- **P1 (A,B) — Domain-neutral SACRED**: vi phạm CLAUDE.md (brand/service literal trong code).
  Phải fix dù là secondary path. Rẻ + an toàn (đổi key/config). **Làm trước.**
- **P2 (C,D) — Correctness alignment**: C đồng bộ happy-case markdown (secondary nên hạ nhẹ);
  D là BLOCKER thật (parent chunk thiếu narrate → embed parent kém). D ưu tiên hơn C.
- **P3 (E,F) — Upload robustness**: E BLOCKER nhưng chỉ kích khi prose-có-pipe (hiếm ở happy-case);
  F là design-choice (heading-as-block) — có thể KHÔNG fix (HDT đã xử). Đánh giá trước khi sửa.
- **P4 (G,H) — Class-specific**: G minor (fallback OK); H cần ADR (đổi retrieval engine) — defer.

→ **Thứ tự ship**: A → B → D → C → E → (F đánh giá) → G → (H ADR).

---

## 3. PER-ITEM — TDD + verify (BẮT BUỘC test trước)

Mỗi item:
1. **Test trước** (failing test reproduce): unit test cho fix đó.
2. **Surgical fix**: đúng tầng, minimal diff, trace về register.
3. **Verify**: (a) test mới pass, (b) full pytest no-regression, (c) **load-test 23 câu ≥ 22/23**
   (B/C/D đụng answer → bắt buộc), (d) grep domain-neutral = 0 (A/B).
4. **Commit riêng** (1 item = 1 commit, prefix `[T1/T3] fix(<flow>): <id> ...`).

### A — narrate VN prompt → config
- Test: `build_narrate("llm")` với locale khác → prompt KHÔNG hardcode VN.
- Fix: prompt từ `language_packs[locale]` / `system_config.narrate_prompt_*`, default generic.

### B — generate price field literal (COUPLED)
- ⚠️ Sửa CẢ `generate.py` (viết) + `conversation_state.py:193` (đọc `price_buoi_le`). Test price-lock
  flow trước/sau để KHÔNG vỡ booking continuity.

### C — generate CSV-extract → stats/markdown
- Test: chunk markdown `| Tên | Giá |` → extract giá đúng (hiện CSV-split fail). Ưu tiên dùng
  `ParsedEntity` (đã có), tránh re-parse.

### D — parent narrate (BLOCKER)
- Test: ingest doc có parent-child → parent chunk có narrate metadata. Verify embed parent.

### E — blocks regex
- Test: prose "Giá | trị" (1 pipe) → KHÔNG bị tag `table`. Tái dùng `_looks_header` (đã domain-neutral).

---

## 4. Done-criteria (đánh dấu DONE khi đủ)
- [ ] P1: A + B done, grep domain-neutral generate.py + llm_narrate.py = **0 literal**.
- [ ] P2: C + D done, load-test ≥ 22/23, parent narrate verified.
- [ ] P3: E done (F đánh giá: fix hoặc ghi "không cần").
- [ ] P4: G done (H → ADR riêng nếu user cần).
- [ ] Sau MỖI item: `verify_query_flow.py` + `verify_happy_case_pipeline.py` GREEN, full pytest pass.

---

## 5. Compliance (CLAUDE.md) — check mỗi commit
- ✅ Sacred #10: KHÔNG app-inject/override answer (A/B/C chỉ đổi extract/config, không chèn text).
- ✅ Domain-neutral: A/B gỡ literal → 0.
- ✅ HALLU=0: C/D answer vẫn grounded.
- ✅ No psql sysprompt (G nếu đụng prompt → alembic/admin-UI).
- ✅ Zero-hardcode · no-version-ref · narrow-except.
- ✅ T1/T3 declared mỗi commit · TDD · ship từng cái.

---

## 6. Anti-pattern CẤM
- ❌ Fix nhiều item 1 commit (khó rollback) — 1 item = 1 commit.
- ❌ Sửa B mà quên `conversation_state.py` (coupled) → vỡ price-lock.
- ❌ Fix C/D mà không chạy load-test → không biết answer có tụt.
- ❌ Đụng retrieval engine (H) mà không ADR.
- ❌ Bịa "đã fix" khi chưa có test pass + load-test number.
