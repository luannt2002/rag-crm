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

## 1. Root cause (evidence-driven, đã trace live)

### 🔴 GAP-1 — B4: stats-synthetic đè raw chunk (SYSTEMIC, cả 2 bot) — false-refuse
- **Bug**: câu hỏi TEXT (ưu đãi / quy trình / công nghệ) mà keyword khớp entity → stats-route serve **synthetic chunk score=1.0** (bảng giá) → **đè raw chunk** có đáp án → LLM không thấy → từ chối oan.
- **Evidence**: spa S-037 "Ưu đãi mua 2 buổi Ultherapy" live → `chunks_used=1, top=1.0`, served = `"CSD Chuyên sâu: 700000 | col_2: x…"` (SAI chunk); corpus CÓ promo "tặng 1 buổi Ultherapy cổ" ở raw nhưng bị đè. xe: brand false-deny cùng cơ chế (Landspider synthetic đè Rovelo raw).
- **Immutable cause**: `query_graph._do_stats_lookup` build synthetic chunk score=1.0 + suppress doc-level fallback khi synthetic built ([:2504,:2590-2625]) — KHÔNG gate theo "câu có hỏi số/giá không".
- **Fail đóng được**: xe false-refuse (≥6) + spa false-refuse (4) = **~10**.

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

### PHASE 1 — B4 intent-gate stats-synthetic (đòn bẩy cao nhất, ~10 fail)
- **P1.1** RED: câu TEXT-intent (ưu đãi/quy trình/công nghệ/mô tả) keyword-match entity → synthetic KHÔNG được đè raw; raw chunk (có promo/process) tới LLM.
- **P1.2** Fix `_do_stats_lookup`: gate suppression theo **"câu có expect number/price"**. Signal: có price-word / code / range / aggregate-intent → serve synthetic (đè OK). KHÔNG có (câu text) → **giữ raw chunks** (serve synthetic NHƯ 1 chunk bình thường, KHÔNG score=1.0 độc quyền, KHÔNG suppress doc-fallback). Domain-neutral (shape/intent, no bot/brand literal).
- **P1.3** đo N≥10: spa S-037/046/075 + xe brand-false-deny → kỳ vọng flip refuse→answer. Kiểm HALLU không tăng (raw thêm ≠ bịa số nhờ numeric-fidelity).
- **Rollback**: nếu HALLU-số tăng (raw chunk kéo số lạ) → revert, xem lại gate.

### PHASE 2 — World-knowledge fabrication (~9 HALLU)
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
