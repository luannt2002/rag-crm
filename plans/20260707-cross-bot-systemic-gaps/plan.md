# [T1-Smartness] Cross-bot systemic gaps — B4 stats-suppress-raw + world-knowledge HALLU + measurement rigor

**Ngày**: 2026-07-07 · Nhánh: `fix-260623-ingest-expert` · Bots: chinh-sach-xe (lốp) + test-spa-id (spa)
**Chuẩn**: CLAUDE.md — rule#0 no-guess, RED-test-first, one-change-per-step, đo N≥10, domain-neutral, sacred #10 (no app-inject/override), evidence-only.

---

## 0. Case study — cách phát hiện (giá trị của multi-bot testing)

Sau khi ship A1/A4/A2/B3/B1 (ADR-0008, DSI name fix), chạy **full agent-graded eval trên 2 bot KHÁC DOMAIN**:
- **xe** (lốp, 200q): gate 91/100 · trap 83/100 · HALLU 12 (`step21_full200_postA4_verdicts.json`).
- **spa** (thẩm mỹ, 100q): ok 83/100 · HALLU 5 (`spa100_domain_neutral_verdicts.json`).

Cross-classify fail 2 bot → **4 tầng lỗi**, trong đó tách được (bằng trace live, rule #0) **2 systemic THẬT vs 2 artifact ĐO**. Chính việc test bot thứ 2 (spa) đã: (a) lộ + fix bug A4 category-collision (commit `2ad4df7`), (b) xác nhận fail-classes GIỐNG NHAU 2 bot ⇒ **domain-neutral** (không per-bot), (c) phân biệt được lỗi thật vs artifact đo.

---

## 0.5. Verified fail counts (rule #0 — re-run 43 fail LIVE)

> Full report: `specs/002-deepdebug-luannt/evidence/fail_verify_analysis_20260707.md`.
> Harness: `scratchpad/fail_verify.py` (fresh connect_id, coref multi-turn) → `fail_verify_result.json` → `fail_class.json`. Code đo: commit `2ad4df7`.

**Eval báo 43 fail. Re-run từng câu LIVE → 43 phóng đại ~1/3:**

| Loại | Count | Bản chất |
|---|---:|---|
| NOTFAIL | 5 | eval sai (load-rỗng S-019 / grading-miss G-068/G-074 / honest-bait S-088/S-064) |
| EMPTY | 3 | B-050/B-052 coref-hard-empty, S-048 |
| NDHALLU | 6 | HALLU-số **KHÔNG tái hiện** (B-002/005/006/035/055/066 → re-run defer đúng) |
| **artifact/non-determ** | **14 (33%)** | — |
| BRAND | 10 | STABLE false-deny (Rovelo ×6 + B-012 + spa S-037/046/075) — **tái hiện 100%** |
| WK | 5 | world-knowledge bịa (S-044/045 "Hàn Quốc", S-006 "thang máy", S-056, B-031 "lốp xe tải") |
| COREF | 4 | sai referent DÙ multi-turn (B-060/B-047, S-058/S-068) |
| COVER | 10 | aggregation / arrival-intermittent (G-063/064/067 refuse nhưng G-068 serve) / comparison-partial / clarify |
| **stable-real** | **29 (67%)** | (3 arrival intermittent → stable "cứng" ≈ 26) |

**Chỉnh giả thuyết TRƯỚC verify:**
- ✅ **BRAND (B4 stats-suppress) = 10 STABLE** → đòn bẩy #1 xác nhận (không phải ~10 mơ hồ).
- 🔽 **World-knowledge = 5 stable** (không phải ~9) — nhưng confident, vẫn fix.
- 🔽 **HALLU-số = INTERMITTENT** — 6/6 bẫy-số re-run defer đúng; eval-1-shot phóng đại; đo lại N≥10 qua numeric-fidelity observe.
- 🔼 **Coref KHÔNG chỉ artifact fresh-id** — 4 COREF + 2 EMPTY tái hiện multi-turn ⇒ **referent-resolution yếu THẬT**; nâng ưu tiên nhẹ nhưng cần multi-turn harness (P0) đo rate trước.
- ⚪ **Empty = 3** (load + coref-hard), guard deterministic đóng.

---

## 1. Root cause (evidence-driven, đã trace live)

### 🔴 GAP-1 — B4: stats-synthetic đè raw chunk (false-refuse) — **TRACE 2026-07-08: 2 ROOT-CAUSE**

Trace LIVE (debug=full, rule #0) 2 đại diện → "10 BRAND" **KHÔNG cùng 1 cơ chế**, tách 2 nhánh:

**GAP-1a — spa descriptive-suppress (TRUE B4, ~3 fail: S-037/046/075)**
- **Bug**: câu TEXT (ưu đãi/quy trình/combo) keyword khớp entity → serve **synthetic bảng-GIÁ score=1.0** → **đè raw** có promo/process → LLM không thấy → refuse oan.
- **Evidence**: S-037 live → `chunks_used=1`, served[0]=`stats_index_synthetic` "CSD 700000 | Meso 3000000 | Nano collagen 2500000…" (bảng giá, KHÔNG có ưu đãi). raw promo bị đè.
- **Immutable cause**: `query_graph._do_stats_lookup` return `synthetic_chunks if synthetic_chunks else linked_chunks` (:2675) + suppress doc-fallback (:2649) — không phân biệt price-ask vs descriptive.
- **FIX-ATTEMPT-1 (đo 2026-07-08 → REVERTED)**: `stats_keep_raw_on_text_intent` = non-price keyword answer serve synthetic **+ doc-fallback raw**. **Đo A/B trên spa (rule #0): VÔ HIỆU** — S-037 base n=1→ON n=21 nhưng **vẫn refuse**; S-075 n=1→10 vẫn refuse. Nguyên nhân: doc-fallback chỉ lấy chunk **CÙNG doc price-sheet = thêm dòng GIÁ**, không phải answer mô tả **cross-doc**. Controls (price-ask/list) không regress. → Simplicity-First + không giữ code không-đạt-mục-đích → **revert**.
- **Rule #0 corpus-check**: S-046 "16 bước" trả **ĐÚNG** live (eval chấm sai, không phải fail). S-037 promo "2 buổi full-face" cụ thể **không có** trong corpus (Ultherapy 8tr có, nhưng khác câu) → refuse **borderline honest**. S-075 (Nâng cơ+Ultherapy đều có) = false-refuse thật nhưng comparison/advice cross-doc. → **cluster spa false-refuse YẾU hơn eval-grade** (giống 43→29, tiếp tục co lại khi trace live).
- **FIX ĐÚNG (chưa làm, work-block đo riêng)**: answer mô tả nằm cross-doc → do **vector/hybrid arm** retrieve semantic, mà stats-synthetic đè. Fix = race-resolver **prefer/merge vector arm** cho non-price descriptive (KHÔNG phải doc-fallback). Rủi ro: đè lên guard neighbour-HALLU → measure N≥10 trước.

**GAP-1b — xe Rovelo brand-narrow (root KHÁC, ~7 fail: B-010/011/012, G-076/077/078/079)**
- **Bug**: "Lốp Rovelo 195/55R16 giá?" → served **LANDSPIDER** 195/55R16 (SAI brand, priced) chứ không phải Rovelo (cùng size, price=NULL) → LLM "chưa phân phối Rovelo".
- **Evidence**: G-077 live → `stats_name_by_shape` CHẠY (tên mô tả đúng) NHƯNG served="Lốp xe LANDSPIDER 195/55R16… 1044000", Rovelo vắng mặt.
- **Immutable cause chain**: `_parse_code_query` size "195/55R16" → `query_by_name_keyword(require_value=True)` → **value-gate LOẠI Rovelo price-NULL** → chỉ còn LANDSPIDER (priced) → 1 entity → **brand-aware skip** (cần >1) → LLM thấy sai brand. 002-G retry-without-value chỉ fire khi entities RỖNG (ở đây LANDSPIDER non-empty → không retry).
- **FIX (chưa ship — HALLU-risky)**: khi brand-aware ON + query có brand-token, size-lookup retry WITHOUT value-gate → surface mọi brand → narrow về Rovelo → serve price-absent. Rủi ro: surface price-NULL cạnh priced sibling = đúng neighbour-HALLU mà value-gate chặn → **PHẢI measure N≥10 trước**. Interim an toàn: bật brand-scope BLOCK (B1 đã ship) → "chưa phân phối Rovelo" → oos_template (chặn denial sai, chưa restore coverage).

### 🔴 GAP-2 — World-knowledge fabrication (SYSTEMIC, cả 2 bot) — HALLU phi-số
- **Bug**: LLM tự thêm **detail phi-số** (marketing/feature/spec) KHÔNG có trong served chunk, từ training knowledge.
- **Evidence**: spa S-044 live → served 4 chunk triệt-lông FAQ (0 hit "Hàn Quốc"), LLM trả "Diode Laser **lạnh của Hàn Quốc**". xe B-031/035/055/066: bảo hành-scope / độ-sâu-gai 8-9mm / marketing.
- **Immutable cause**: numeric-fidelity gate CHỈ soi số (`classify_answer_numbers`) → **MÙ với fabrication phi-số**. Không có gate/rule chặn text ngoài corpus.
- **Fail đóng được**: xe world-knowledge (4) + spa (5) = **~9 HALLU**.

### ⚠️ GAP-3 — Empty answer (artifact LOAD + gap nhỏ thật)
- **Evidence**: spa S-019 eval RỖNG (0 chunk) NHƯNG trace live NGAY `chunks_used=3, top=0.92, trả đúng`. → retrieval degrade dưới tải đồng thời (sem=8), KHÔNG phải bug per-query.
- **Gap thật**: 0-chunk → bot trả **RỖNG** thay vì `oos_answer_template`. Cần **empty-answer guard** (deterministic).

### ⚠️ GAP-4 — Coref (artifact ĐO, KHÔNG kết luận được)
- **Evidence**: eval dùng **fresh connect_id MỖI câu** (để tránh stale-history artifact ở xe) → câu follow-up ("quy trình của **nó**") mất referent → bot đoán bừa. → coref "fail" KHÔNG đáng tin.
- **Cần**: multi-turn eval (setup + follow-up cùng hội thoại) mới đo được coref THẬT.

---

## 2. Chiến lược (ladder: 1 change → đo → next; EVOLVE không rewrite)

Thứ tự = **measurement rigor TRƯỚC** (để mọi đo sau honest), rồi 2 fix systemic theo đòn bẩy.

### PHASE 0 — Measurement rigor (đóng artifact, không phải bug pipeline)
- **P0.1 empty-answer guard** (cũng đóng GAP-3 gap-thật): guard_output / generate — nếu `answer` rỗng/whitespace → trả `oos_answer_template` (sacred #10: owner text, không app-inject). RED test: empty answer → template. Deterministic.
- **P0.2 multi-turn eval harness**: script hỏi câu SETUP rồi câu FOLLOW-UP **cùng connect_id** (giữ history) + reset connect_id GIỮA các cặp (tránh stale cross-case). Đo lại coref cases (xe B-047/050/052/060, spa S-058/064/068) → biết coref THẬT bao nhiêu.
- **P0.3 eval concurrency**: giảm sem 8→4 hoặc thêm retry-on-0-chunk để loại artifact load khỏi số eval.
- **Gate**: sau P0, re-measure → gate/trap/HALLU "sạch artifact".

### PHASE 1 — B4 intent-gate stats-synthetic (đòn bẩy cao nhất, **10 fail STABLE** verified)
- **P1.1** RED: câu TEXT-intent (ưu đãi/quy trình/công nghệ/mô tả) keyword-match entity → synthetic KHÔNG được đè raw; raw chunk (có promo/process) tới LLM.
- **P1.2** Fix `_do_stats_lookup`: gate suppression theo **"câu có expect number/price"**. Signal: có price-word / code / range / aggregate-intent → serve synthetic (đè OK). KHÔNG có (câu text) → **giữ raw chunks** (serve synthetic NHƯ 1 chunk bình thường, KHÔNG score=1.0 độc quyền, KHÔNG suppress doc-fallback). Domain-neutral (shape/intent, no bot/brand literal).
- **P1.3** đo N≥10: spa S-037/046/075 + xe brand-false-deny → kỳ vọng flip refuse→answer. Kiểm HALLU không tăng (raw thêm ≠ bịa số nhờ numeric-fidelity).
- **Rollback**: nếu HALLU-số tăng (raw chunk kéo số lạ) → revert, xem lại gate.

### PHASE 2 — World-knowledge fabrication (**5 HALLU STABLE** verified; NDHALLU-số=6 intermittent theo dõi riêng)
- **P2.1** Chọn 1 trong 2 (đo, không đoán):
  - (a) **Owner sysprompt anti-fabricate** (sacred #3/#10, ADR-W1-S10 append governed): "chỉ nói điều CÓ trong tài liệu; không thêm xuất xứ/công nghệ/đặc điểm ngoài tài liệu". LLM tự kiểm. Đo lift.
  - (b) **Non-numeric grounding gate** (deterministic): mở rộng grounding-judge / n-gram check cho claim phi-số (brand/origin/feature) — flag khi answer chứa proper-noun/claim không có trong served context. Rủi ro false-block cao → observe trước.
- **P2.2** đo N≥10: spa S-044/045 + xe B-031/055/066 → kỳ vọng bớt bịa "Hàn Quốc"/marketing. Kiểm coverage không tụt (đừng over-refuse).
- **Ưu tiên (a) trước** (owner-layer, sacred-compliant, 0 code-risk); (b) chỉ khi (a) lift thấp.

### PHASE 3 — Re-eval 2 bot (đóng vòng)
- Chạy lại full eval xe 200q + spa 100q (agent-graded, DB-verified, fresh-id + multi-turn cho coref) sau P0-P2.
- **Success**: false-refuse ↓ (B4), world-knowledge HALLU ↓ (P2), empty=0 (guard), coref đo được thật; gate/trap không regress; HALLU-số vẫn 0.

---

## 3. Files (dự kiến — surgical)

| Phase | File | Thay đổi |
|---|---|---|
| P0.1 | `orchestration/nodes/guard_output.py` hoặc `generate.py` | empty→oos_template guard |
| P0.2 | scratchpad/`eval_multiturn.py` (mới) | multi-turn harness |
| P1.2 | `orchestration/query_graph.py` `_do_stats_lookup` | intent-gate synthetic suppression |
| P1/P0 | `shared/constants/*` | flag `stats_suppress_raw_price_only` (default OFF→per-bot) |
| P2a | alembic (per-bot sysprompt append, governed) | anti-fabricate default rule |
| P2b | `guard_output.py` + `shared/` | non-numeric grounding gate (nếu cần) |
| all | `tests/unit/*` | RED tests mỗi phase |

## 4. Success criteria (đo, không đoán)

- [ ] P0.1: empty answer = **0** (mọi 0-chunk → template). RED→GREEN.
- [ ] P0.2: coref đo lại multi-turn → biết số THẬT (thay artifact).
- [ ] P1: false-refuse ↓ ≥50% cả 2 bot (N≥10 mỗi cluster); HALLU-số không tăng.
- [ ] P2: world-knowledge HALLU ↓ (spa S-044/045 + xe B-055/066); coverage không tụt.
- [ ] P3: re-eval — gate/trap không regress, HALLU-số=0, false-refuse+world-knowledge giảm rõ.
- [ ] Domain-neutral: mọi fix 0 per-bot/brand literal; flag per-bot; verify grep.

## 5. Rủi ro & rollback

- B4 gate quá lỏng → raw chunk kéo số lạ → HALLU-số tăng → numeric-fidelity block phải giữ; revert nếu tăng.
- Non-numeric gate (P2b) false-block cao → observe-only trước, đo FP.
- Empty-guard che retrieval-perf thật → vẫn log 0-chunk event để không giấu perf issue.

## 6. Không làm (scope guard)

- KHÔNG rewrite stats subsystem (evolve). KHÔNG F1 price→attribute (ADR-0007, program riêng). KHÔNG per-bot hardcode. KHÔNG fix retrieval-under-load ở đây (perf riêng — chỉ log + empty-guard).
