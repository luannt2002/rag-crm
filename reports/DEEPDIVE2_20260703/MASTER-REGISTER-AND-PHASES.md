# MASTER REGISTER + PHASED PLAN — trả lời "giải hết mâu thuẫn chưa? plan mấy phase?"

> Hợp nhất TẤT CẢ finding (dedup từ ~167 dòng thô của 29 report pass-1 + 5 report pass-2)
> thành 1 register: mỗi finding 1 dòng, có trạng thái + gốc rễ + fix + effort + phase + test.
> Đây là cái pass trước THIẾU (chưa có 1 index hợp nhất). Full case-study ở report con.

---

## §1. TRẠNG THÁI THẬT (không nói quá)

| Hạng mục | Trạng thái |
|---|---|
| **Report chi tiết mọi luồng** | ✅ ĐỦ — 29 report pass-1 (mọi thư mục src) + 5 report pass-2, mọi finding có `file:line` |
| **Plan chi tiết (case-study problem→root→fix→expert→tradeoff→impact)** | ✅ cho finding CONFIRMED nặng (PLAN Phần B + pass-2 reports, ~180 case-study/verdict); ⚠️ trước đây THIẾU 1 register hợp nhất → **file này bổ sung** |
| **Mâu thuẫn giữa agent** | ✅ giải ~hết (xem §2); còn **1 residual** cần runtime trace |
| **BUG đã FIX** | ❌ **0/52 fixed** — cố ý (anh dặn "phân tích+report+plan TRƯỚC khi làm") |
| **Bằng chứng runtime + guard** | ✅ 16 test đỏ (test_audit_pass2_repro.py + 2 pin) = chứng minh + regression guard |

**Kết luận thẳng:** VẤN ĐỀ (bug) **CHƯA giải quyết** — mới xong analysis+plan+failing-test. MÂU THUẪN giữa các agent **đã giải gần hết** (1 còn treo). Đây đúng là điểm nên dừng để anh duyệt plan TRƯỚC khi fix (không lật lại bug cũ).

---

## §2. LEDGER MÂU THUẪN — đã giải chưa?

| Mâu thuẫn (agent A vs B) | Verdict sau khi đọc lại source | Trạng thái |
|---|---|---|
| Idempotency key: header (`X-Idempotency-Key`) vs source_url | Đọc `idempotency_key.py:40` + `ingest_document.py:58`: `for_ingest_document(tenant, source_url, corpus_version)` — **source_url-based, KHÔNG bot_id**. X-agent đúng, critic nhìn nhầm layer | ✅ GIẢI |
| S1: "11 key" vs "22 key" drop | AST walk = 22 (pass-1 undercount); test T1 xác nhận 5 key cross-node quan trọng đều undeclared | ✅ GIẢI (22) |
| Re-export gãy "7 test" vs "5 test" | 5 (re-export `24f2451`) + 3 (FastAPI env 0.135) = 2 root khác nhau | ✅ GIẢI (5) |
| XML-wrap "100% chết" vs "còn opt-in" | `_pcfg(xml_wrap_enabled)` đọc key declared → opt-in CHẠY; chỉ date-default-on chết | ✅ GIẢI (partial) |
| int(_price) "HALLU nặng" vs "0 tác động" | Mọi bot đang VND (int no-op); chỉ cắn bot USD/EUR → gap domain-neutral, không breach hôm nay | ✅ GIẢI |
| RLS "leak fallback" vs "fence giữ" | `record_bot_id` UUID unique giữ fence → không cross-tenant leak; bug thật = **soft-delete resurrection** | ✅ GIẢI (reframe) |
| stats "fabricated tenant uuid" | Không có trên production write; = demo `UUID(int=1)` + docstring GUC-name sai | ✅ GIẢI (reframe) |
| **Re-ingest "xóa 99 entity"** — L1 CONFIRMED coupling vs X OVERCLAIMED (per-doc, guarded) | X đúng ở mức "per-`doc_id`, không cross-document"; nhưng residual thật: re-parse 1 doc ra tập nhỏ-hơn-non-empty VẪN xóa entity tốt của **doc đó**. Mức độ (systemic vs edge) **cần 1 runtime ingest trace để chốt** | ⚠️ **RESIDUAL** — mechanism rõ, severity cần đo |

→ **9/9 mâu thuẫn đã đọc source giải rõ mechanism; 1 còn treo phần SEVERITY** (cần runtime trace, đã ghi ở PLAN §E.5).

---

## §3. REGISTER HỢP NHẤT — 52 finding actionable (dedup), gán phase + test

Cột: **V**=verdict pass-2 (C=Confirmed, R=Refined, O=Overclaimed-hạ-cấp) · **Sev** · **E**=effort · **T**=có repro-test(✓)

### GUARD cấu trúc (làm Phase 0 — diệt cả class)
| ID | Guard | Chặn class | E |
|---|---|---|---|
| G1 | AST pin-test used⊆GraphState | S1 state-drop | S |
| G2 | Wiring-audit + integration test un-mocked (cấm AsyncMock) | S2 built-not-wired | M |
| G3 | Merge-gate: block collection-error + pin-fail vào main | "integrate merge nuốt fix" | S |
| G4 | Shape-only header + canary corpus | S3 happy-case-zero | M |

### LUỒNG 1 — INGEST (20 finding)
| ID | Finding | V | Sev | Gốc rễ (1 dòng) | Fix (1 dòng) | E | T | Phase |
|---|---|---|---|---|---|---|---|---|
| I1 | Path A/B split (worker flatten) | C | CRIT | worker parse-path song song, không truyền raw_bytes | canonical seam / truyền row-shape | M | | 2 |
| I2 | OCR fallback 0-block mọi doc | C | CRIT | gọi async `extract_bytes` sync | `extract_bytes_sync` + un-mock contract test | S | | 2 |
| I3 | .doc/.xls/.ppt no parser | C | HIGH | không có OLE2 sniff + adapter | thêm parser adapter | M | | 2 |
| I4 | PII dead 2 tầng | C | CRIT | bootstrap đóng băng null + config gate | `providers.Callable(get_boot_config)` | S | | 2 |
| I5 | Happy-case box → 0 entity | C | HIGH | header vocabulary-gated | shape-only fallback (G4) | M | ✓ | 3 |
| I6 | Coverage gate no-repair | C | HIGH | `uncovered_spans` tính rồi vứt | append span → tail chunk (~15 dòng) | S | | 2 |
| I7 | Re-ingest xóa stats doc đó | R | MED | delete_by_document per-doc | dedup/incremental stats | M | | 3 |
| I8 | AdapChunk chưa real (0/8 oracle) | C | HIGH | selector chọn trước chunk | bake-off→feedback loop | L | | 4 |
| I9 | int(_price) truncation | R | MED | render `int()` giá NUMERIC | carry Decimal/str | S | ✓ | 3 |
| I10 | cleaner xóa dòng lặp ≥3 | C | MED | heuristic header/footer | gate theo vị trí, không content | S | | 3 |
| I11 | language=auto→vi | C | MED | không detect ngôn ngữ | wire language detect | S | | 3 |
| I12 | asyncpg 32k bind ceiling | C | MED | 1 INSERT mọi row | batch INSERT | S | | 2 |
| I13 | tool_name collision→chimera | C | HIGH | slug title là namespace duy nhất | thêm hash/bot vào key | S | | 2 |
| I14 | 3× litellm gọi thẳng bypass router | C | MED | không qua port | route qua LLM port | M | | 4 |
| I15 | deterministic_chunk_id PK collision | C | LOW | UUID5(content) trùng khi row lặp | +index vào seed | S | | 4 |
| I16 | page_number không persist | C | MED | không ghi metadata_json | ghi page vào metadata (no migration) | S | | 2 |
| I17 | diff_reingest NameError landmine | C | MED | hàm ở dead module | xóa flag hoặc implement | S | | 2 |
| I18 | money-shape quyết định structure | C | MED | `_is_pure_money` gate header | shape-first (kèm G4) | M | | 3 |
| I19 | CSV `;`/UTF-16 crash/skip | C | MED | chỉ nhận comma/leading-pipe | mở delimiter + encoding ladder | S | | 3 |
| I20 | stats fabricated tenant (demo) | O | LOW | `or uuid.uuid4()` fail-open | fail-loud + fix docstring | S | | 4 |

### LUỒNG 2 — QUERY (19 finding)
| ID | Finding | V | Sev | Gốc rễ | Fix | E | T | Phase |
|---|---|---|---|---|---|---|---|---|
| Q1 | S1 state-key drop (22 key) | C | CRIT | GraphState không khai key | khai key + AST pin (G1) | S | ✓×5 | 1 |
| Q2 | Stats route 0 verify + HALLU-net revert | C | CRIT | `3097755` revert `062d6fa` | restore gate + merge-gate (G3) | S | ✓ | 1 |
| Q3 | GraphRAG kwarg 2 chiều | C | CRIT | `bot_id=` vs `record_bot_id` | rename kwarg + un-mock test | S | ✓×2 | 1 |
| Q4 | Grounding gate NGƯỢC | C | HIGH | warn không block; fail-closed refuse | owner chốt: escalate hay observe | M | | 1 |
| Q5 | ai_keys schema không tồn tại | C | CRIT | `ragbot.` prefix | bỏ prefix (5 dòng) | S | ✓ | 1 |
| Q6 | Cascade routing no-op | C | HIGH | resolved_answer_model 0 reader | wire hoặc xóa | S | | 3 |
| Q7 | parent_chunk_id không SELECT | C | HIGH | SQL không project cột | +1 cột SELECT → sống 3 feature | S | | 3 |
| Q8 | count≠list + price-range OR/AND | C/R | HIGH/hẹp | match set khác; bug "any" cross-col | đồng bộ fold + fix range | M | | 3 |
| Q9 | heuristic 0.85≥0.85 + locale không wire | C | HIGH | threshold=conf + signals không truyền | `>` + truyền signals (load-test) | M | | 3 |
| Q10 | per-bot embed dim bỏ qua + vector(1280) khóa | C | HIGH | adapter ghim matryoshka | validate dim at binding + config | M | | 3 |
| Q11 | SPECULATIVE_REDO_SENTINEL leak answer | C | HIGH | redo protocol chưa impl | implement hoặc gate off | M | | 1 |
| Q12 | reranker construct mỗi turn (CB defeat) | C | HIGH | không cache adapter | singleton per binding | M | | 3 |
| Q13 | LiteLLMReranker lệch index chunk rỗng | C | HIGH | index map bỏ empty | giữ index gốc | S | | 1 |
| Q14 | streaming no failover + fallback cost primary-price | C/R | MED | ModelRuntimeConfig thiếu fallback_pricing | +field + failover | M | | 4 |
| Q15 | re-export gãy → 5 collection error | C | HIGH | `24f2451` xóa import | restore re-export (Phase 0) | S | ✓×3 | 0 |
| Q16 | RLS dead superuser + fallback bare session | C/R | HIGH | superuser DSN + kwargs nuốt tenant | ops provision + fallback fence | M | | 1/4 |
| Q17 | soft-delete resurrection fallback | C | HIGH | thiếu `deleted_at IS NULL` | +filter 3 stage | S | | 1 |
| Q18 | GraphRAG chunk_id=None dropped | C | MED | synthetic id falsy | gán synthetic id | S | | 1 |
| Q19 | int(_price) (=I9) | R | MED | (dup) | (dup) | | ✓ | 3 |

### LUỒNG 3 — OBS/EVAL/TEST (8 finding)
| ID | Finding | V | Sev | Gốc rễ | Fix | E | Phase |
|---|---|---|---|---|---|---|---|
| O1 | Test suite hỏng cửa (8 collection err) | C | HIGH | re-export + FastAPI env | Phase 0 unbreak | S | 0 |
| O2 | Verification tier thiếu (numeric/citation/completeness) | C | HIGH | không có post-generate check | +node observe-only | M | 3 |
| O3 | Redis recovery không re-dispatch | C | HIGH | XCLAIM rồi vứt payload | dispatch/XAUTOCLAIM | M | 2 |
| O4 | InvocationLogger finally không guard | C | MED | INSERT không try/except | wrap try/except | S | 1 |
| O5 | webhook_dispatcher sai exception class | C | MED | catch builtin, redis raise riêng | catch RedisError | S | 2 |
| O6 | grounding warn-vs-block inventory | C | MED | nhiều "safety" chỉ warn | kiểm kê + chốt block | S | 1 |
| O7 | ~25-30% test không bắt behavioral break | C | MED | source-regex pin + dead-park | chuyển behavioral | L | 4 |
| O8 | 33 xpass stale mark | C | LOW | mark cũ | dọn mark | S | 4 |

### SECURITY/TENANT (4 finding — nhiều cái đã nằm trong Q/I)
| ID | Finding | V | Sev | Fix | Phase |
|---|---|---|---|---|---|
| S-1 | Middleware order tắt CORS+3 RL | C | HIGH | TenantContext add cuối + regression test | 1 |
| S-2 | RLS dead runtime (superuser) | C/R | HIGH(posture) | ops provision ragbot_app (sau S-4) | 4 |
| S-3 | idempotency thiếu bot_id | C | HIGH | +record_bot_id vào key | 1 |
| S-4 | demo UUID(int=1) + docstring drift | O | LOW | giữ gateway-block + sửa docstring | 4 |

---

## §4. PLAN = **5 PHASE** (Phase 0 → 4)

| Phase | Tên | Nội dung | Điều kiện xong (gate) | Thời lượng |
|---|---|---|---|---|
| **P0** | Guards + un-break CI | G1 AST-pin · G3 merge-gate · Q15/O1 restore re-export + FastAPI shim | `pytest` chạy được (0 collection error); 3 guard active | ~0.5 tuần |
| **P1** | CRITICAL: HALLU · Revenue · Security-crit | Q1 state-key(→5 test xanh) · Q2 HALLU-net(→pin xanh) · Q3 GraphRAG(→2 test xanh) · Q5 ai_keys(→test xanh) · S-1 middleware · S-3 idempotency(→test xanh) · Q17 soft-delete · Q11 sentinel-leak · Q13 rerank-index · Q4 grounding-gate(owner chốt) · O4/O6 | 8 test đỏ→xanh; HALLU-net kín; sacred #10 verify | Tuần 1 |
| **P2** | Multi-format + ingest correctness | I1 Path A/B · I2 OCR · I3 .doc/.xls · I4 PII · I6 coverage-repair · I13 tool_name · I16 page · I12 bind-ceiling · I17 landmine · O3 Redis-recovery · O5 webhook · G2 wiring-audit | ingest mọi format qua Path B; PII bật; wiring-audit pass | Tuần 2 |
| **P3** | Escape happy-case + verification tier + retrieval | I5 shape-header(G4)+canary · I9/I18/I19 currency+delimiter · Q7 parent_chunk · Q8 count/range · Q9 heuristic · Q10 dim · Q12 rerank-cache · O2 verification-node · Q6 cascade · I7/I8/I10/I11 | canary >0 entity; verification tier observe-only live; load-test đo lift | Tuần 2-3 |
| **P4** | Eval end-to-end + ops + long-tail | Agent-Grader RAGAS + ground-truth 6-loại + bake-off feedback loop · S-2 RLS cutover(ops) · Q14 streaming-failover · I14/I15/I20 · O7/O8 dọn test | ablation 6-config; RLS bật thật; report lift số thật | Tuần 4+ |

**Nguyên tắc gate:** mỗi Phase chỉ qua khi (a) test đỏ liên quan → xanh, (b) load-test đo (không đoán %), (c) 0 regression, (d) tự-audit CLAUDE.md sacred. Mỗi fix lật đúng 1 failing-test → không bao giờ lại bug cũ.
