# DEEP ANALYSIS — Multi-bot stats-index / retrieval / ingest / eval (2026-06-26)

> Tổng hợp 4 phân tích chuyên sâu (ingest-stats · retrieval · multibot-mechanism · eval-scorer).
> Mindset CLAUDE.md: rule#0 CẤM ĐOÁN (nhãn SỰ THẬT vs GIẢ THUYẾT), no fix sai tầng, domain-neutral, HALLU=0 sacred, Coverage-vs-Faithfulness.
> Scope: CHỈ vấn đề CODE (loại external/data-gap). Gom 3 nhóm A/B/C.

---

## 0. BẢNG TỔNG HỢP

| Nhóm | #fail (quan sát) | Tầng gốc rễ | Effort | Priority |
|---|---|---|---|---|
| **A — Scorer false-negative** (answer đúng bị chấm FAIL) | ~13 flip (5 xe + 8 spa) | eval-harness | S (1 file script, 0 API) | **P0** |
| **B — Render sai số / HALLU** (trả Giá cho câu Kho, sai sibling, prose-miss) | ~7 (xe F3 · spa F7/F8/F11*) | ingest + stats-index + retrieval-route | L (src + alembic + re-ingest) | **P1** |
| **C — Present-but-route-blind / data-gap** (SKU, hotline, Ngày về, stale deleted-doc) | ~6 (xe SKU/hotline/Ngày về + stale 433/496 entity) | ingest (header carry + purge) + retrieval-route | M–L (src + re-ingest) | **P1/P2** |

\* F11 bị **misclassify** sang nhóm B — thực chất là **nhóm A** (golden sai: corpus literal "1-2 buổi", golden ghi "3-5"). Đã reassign.

**Đếm HALLU thật**: chỉ xe F3 (`774.000` cho câu tồn kho) là HALLU fabricate đúng nghĩa — bot lấy attribute Giá thay câu hỏi Kho. spa F8 là chọn sai sibling (cả 2 số đều real). Sacred HALLU=0 đang VỠ tại F3.

---

## 1. BỊ GÌ — danh sách vấn đề CODE (gom A/B/C)

### NHÓM A — Eval-scorer chấm sai (answer ĐÚNG → FAIL)
- A1. Scorer OLD committed dùng exact-token substring, KHÔNG accent-fold, KHÔNG content-word overlap → câu trả lời prose/paraphrase/abbrev-expand không có token exact để hit. `scripts/retest_golden_generic.py` `ok = any(hit(f) for f in facts) if facts else bool(ans.strip())`.
- A2. `key_facts()` regex `[A-ZÀ-Ỹ]{2,}` sinh "fact" rác 2 ký tự ('Đê','Hà' từ địa chỉ) mà `fact_hit()` không bao giờ match (alnum path gate `len(fa)>=4`, digit path cần 3+ digit) → rơi xuống False. Evidence: `reports/GOLDEN_chinh-sach-xe_20260626_175429.json` case 'Địa chỉ kho lốp Nam Phát' pass=False dù ans chứa full address.
- A3. Report JSON lưu `overlap=None` (không có key overlap) → con số headline xe 70% / spa 72% là output của scorer CŨ; scorer MỚI (2-path) **UNCOMMITTED, chưa chạy end-to-end**. (`'overlap' in results[0] == False`).
- A4. Bước nhảy 16:53→17:54 (xe 55→70%, spa 54→72%) là **answer-driven** (LLM non-determinism + nhiều answer rỗng '' ở run đầu), KHÔNG phải scorer-driven — cùng 1 scorer committed.

### NHÓM B — Render sai số / HALLU (CODE bug)
- B1. **Cùng entity 2 ROW rời** cùng `entity_name` ở 2 doc: 1 row chỉ {Giá:774000}, 1 row chỉ {Kho,Mã}. KHÔNG row nào mang cả 2 → LLM nhận entity chỉ-Giá → render 774.000 cho câu tồn kho. Evidence DB bot `c6e1fc56`: doc `389025d0` `{"Giá":774000}`; doc `eaeb34ef` `{"Kho":"Kho lốp ROVELO","Mã":"2-R12C155/12"}`; co-occur BOTH=0/496, dup names 170/325. (xe F3)
- B2. **Stats Self-Query route mù attribute-intent**: route chọn theo SHAPE câu hỏi (price_of_entity conf 0.8 / list keyword), KHÔNG check attribute hỏi có tồn tại trên entity không → `_do_stats_lookup` (`query_graph.py` ~L2401-2413) dump MỌI field-like attr → câu Kho nhận Giá. ILIKE keyword ô nhiễm ('tồn kho lốp Rovelo … còn') = 0 rows → fallback `list_all_entities(100)` dump 100 row gồm Giá:774000. (`retrieve.py` L235-260)
- B3. **Hard short-circuit bỏ qua prose value**: spa F7 'Combo 10 buổi nâng cơ' exp 8.000.000 sống TRONG prose chunk ('Sale chỉ còn 8 triệu'), KHÔNG là structured entity. Route stats conf 0.8 return ngay (`grade.py` L99-111 bypass CRAG), `stats_index_race` OFF → vector branch không bao giờ surface prose. Bot trả 10 triệu (sibling sai). (spa F7)
- B4. **Disambiguation 2 sibling gần giống**: spa F8 'tẩy da chết' — DB có 'Tẩy da chết body'=450000 VÀ 'Tẩy da chết & ủ trắng body'=550000; bot vớ nhầm. Cả 2 số real (KHÔNG fabricate). (spa F8)

### NHÓM C — Present-but-route-blind + stale-data (CODE bug)
- C1. **Stats-index DRIFT**: 433/496 entity (87%) thuộc doc ĐÃ soft-delete (.csv re-ingest thành HTML). `DeleteDocumentUseCase` KHÔNG purge `document_service_index` (`delete_document.py` L55-78 chỉ vector.delete + archive); serving query chỉ `WHERE record_bot_id`, KHÔNG join `documents.deleted_at` (`stats_index_repository.py` L418-446/518-529) → phục vụ entity của corpus CŨ vĩnh viễn. Rovelo 774000 đến từ `xe-3.csv` ĐÃ XÓA.
- C2. **Header 2-dòng bị mangle**: converter `tabular_markdown.py:120` fill ô header rỗng bằng `f"col{i+1}"` NGAY tại tầng markdown → phá tín hiệu gap mà `_premerge_split_headers` (`document_stats.py:766-795`) cần (`_row_has_gaps`). Sinh key rác `col5..col10` (converter) + `col_1..col_7` (fallback document_stats). Chunk `e3320eed/0` literal: `| col1 | Tên kho | Mã hàng | … | col5 | col6 |…`. SKU/Ngày về/link ảnh rơi vào col_N = unretrievable.
- C3. **Header per-chunk reset**: warehouse sheet giữ header ở 1 chunk, 97 data-only chunk khác KHÔNG có header (`document_stats.py:827` loop, reset L852-855) → positional extraction → Mã/Tồn/ảnh thành col_N. DB: chunks-with-header=1 vs data-only=97.
- C4. **Notation gap** (GIẢ THUYẾT): hotline `0988 771 310`=2 chunks vs `0988771310`=0; SKU `2-R12C155`=stats:1 chunks:0. `parse_code_query` trả None trên phrasing test, BM25 space-stripped phone miss spaced token. **CHƯA capture live retrieve trace** → cần debug-trace per-question trước khi fix.
- C5. **Subsystem là PRICE-index, không phải ATTRIBUTE-index** (latent N+1): schema `price_primary/secondary`, query `query_by_price_range/top_by_price`, `parse_money_vn` floor `DEFAULT_PRICE_MIN_VND` drop số nhỏ (tồn '26', RAM '8'). Câu numeric non-price (m², #khoản) không có path. Baseline price-coupling=127. KHÔNG gây 26 fail hiện tại (xe/spa ARE price-catalog) nhưng chặn bot N+1.

---

## 2. LÝ DO (root cause) — chain L1←L2←L3 + immutable cause + tầng

### NHÓM A — eval-harness
- **Chain**: answer-correct-but-FAIL ← scorer không có paraphrase/semantic path ← `key_facts()` là tín hiệu DUY NHẤT và extract junk 2-char ← ok-rule fall-through False khi facts non-empty-but-unhittable.
- **Immutable cause**: OLD scorer = pure exact-token substring, NO accent-fold, NO content-word overlap. **Tầng: eval-harness.** **SỰ THẬT.**
- Scorer MỚI 2-path (specific_facts OR content_words overlap≥0.5) đúng shape generic → offline flip 13 case PASS (address overlap=1.0, warranty 0.5-0.6, Buffet 1.0). **SỰ THẬT.**
- Rủi ro FALSE-POSITIVE tiềm ẩn của scorer mới trên câu giá: overlap≥0.5 có thể PASS khi số SAI là thiểu số token (F7 truncated gold → overlap=0.75 PASS nhầm; full gold → 0.0 FAIL — phụ thuộc số lượng số trong gold = may rủi, không phải design). **SỰ THẬT.**
- Value-equivalence '8.000.000'=='8 triệu' chưa normalize → câu prose số đúng có thể FALSE-NEGATIVE. **GIẢ THUYẾT** (chưa quan sát live).
- **KHÔNG fix sai tầng**: F11 + ~11 NHÓM-A là scorer/golden defect, **KHÔNG** sửa ở retrieval.

### NHÓM B — ingest + stats-index + retrieval-route (đa tầng)
- **Chain (B1)**: render 774.000 cho câu Kho ← LLM chỉ thấy entity {Giá:774000} ← 2 ROW tách rời cùng entity_name không reconcile ← `bulk_insert` pure INSERT per ParsedEntity, KHÔNG ON CONFLICT/merge (`stats_index_repository.py:134-142`) ← `parse_table_chunks` chạy per-document, không cross-sheet entity resolution.
- **Immutable cause (B1)**: KHÔNG có cơ chế ENTITY RECONCILE cross-document. **Tầng: stats-index (ingest).** **SỰ THẬT.**
- **Chain (B2)**: trả wrong-attribute ← stats route fire trên MỌI price/keyword-shape query và short-circuit vector ← route chọn theo SHAPE, MÙ attribute hỏi ← `_do_stats_lookup` dump mọi attr / `list_all_entities` dump 100 row.
- **Immutable cause (B2)**: KHÔNG có attribute-intent signal gate stats route; synthetic-chunk builder emit tất cả field. **Tầng: retrieval.** **SỰ THẬT.**
- **Chain (B3)**: prose value bị miss ← stats route hard short-circuit trước vector ← value sống ONLY trong free-text chunk ← stats ILIKE match sibling sai, không bao giờ tới vector branch.
- **Immutable cause (B3)**: stats route hard short-circuit (`grade.py:99-111`, `retrieve.py:544-586`) không fallback-to-vector; `stats_index_race_enabled` DEFAULT OFF. **Tầng: retrieval.** **SỰ THẬT.**

### NHÓM C — ingest (header/purge) + retrieval (notation)
- **Chain (C1)**: trả số từ corpus đã xóa ← serving đọc entity của doc soft-deleted ← `document_service_index` không bị purge khi xóa doc ← `DeleteDocumentUseCase.execute` chỉ purge vector + archive, KHÔNG gọi stats_index_repo.delete_by_document ← use-case không inject stats_index_repo.
- **Immutable cause (C1)**: DeleteDocumentUseCase thiếu purge stats-index + serving SQL không filter `deleted_at` → stale entity phục vụ vĩnh viễn. **Tầng: ingest.** **SỰ THẬT.**
- **Chain (C2/C3)**: stock/SKU/date thành col_N ← 97 data-only chunk KHÔNG có header ← `_premerge_split_headers` + `_column_roles` PER-CHUNK, header chunk tách rời data chunk; thêm `tabular_markdown.py:120` fill ô rỗng = col{i} TRƯỚC khi tới document_stats, phá tín hiệu gap.
- **Immutable cause (C2/C3)**: header detection/role-assign là per-chunk + converter mangle header 2-dòng tại tầng markdown TRƯỚC document_stats. **Tầng: ingest.** **SỰ THẬT.**
- **Chain (C4)**: SKU/hotline refuse ← query notation ≠ stored notation ← code-route trả None + BM25 space-stripped phone miss spaced token. **Immutable cause**: notation-normalization gap. **Tầng: retrieval.** **GIẢ THUYẾT** — chưa có live trace, cần capture trước fix.
- **Chain (C5)**: bot non-price không có numeric path ← schema + query API price-typed ← subsystem build như PRICE-index, attributes_json chỉ là fallback. **Immutable cause**: 'price' hardcode làm numeric concept của engine. **Tầng: stats-index.** **SỰ THẬT** (latent N+1). `parse_money_vn` floor value: **GIẢ THUYẾT** (=10_000 audit-stated, chưa re-measure).

---

## 3. CÁCH FIX VỚI MULTI-BOT — cơ chế GENERIC + chứng minh bot N+1

> Nguyên tắc chung: KHÔNG hardcode tên bot / tên cột (Kho/Giá/RAM/Tồn/Ngày về) / ngành. Thao tác CHỈ trên CẤU TRÚC tổng quát (entity_name, attributes_json, documents.deleted_at, header-gap/chunk-adjacency shape, query-SHAPE) + token-set role grammar generic (mirror cơ chế price_primary đã có). Verify bằng grep: 0 literal cột/bot/ngành trong diff (ratchet `test_domain_neutral_guard.py`).

### NHÓM A — Scorer 2-path + 3 harden generic
- **Cơ chế**: adopt scorer 2-path (specific_facts OR content_words overlap≥0.5) làm baseline rule-only (KHÔNG LLM-judge, giữ harness deterministic/offline). HARDEN: (1) **PRICE/NUMBER-CRITICAL guard** — nếu gold chứa money/quantity fact, BẮT BUỘC fact đó present (fact_hit) mới PASS, KHÔNG cho overlap≥0.5 một mình pass → giết F7-style false-positive deterministic; (2) **VALUE NORMALIZER** generic canonicalize '8.000.000'/'8 triệu'/'8tr' → int; (3) **ACRONYM-EXPANSION** load từ per-bot `custom_vocabulary` (KHÔNG hardcode). Report 2-tier: Coverage + wrong_number_fails (Faithfulness-proxy).
- **n+1 proof**: bot N+1 (SaaS-pricing): giá guard bởi cùng regex `\d{1,3}([.,]\d{3})+`, '$1,200' canonicalize cùng normalizer, acronym riêng từ custom_vocabulary. KHÔNG sửa scorer code; scorer chỉ đọc gold+answer+vocab (data, not code). overlap path fold accent + stoplist cố định → any-domain prose chạy. LLM-judge = OPTIONAL pluggable Port, default OFF, KHÔNG bao giờ là gate.

### NHÓM B — Entity-reconcile + attribute-aware route + non-destructive
- **Cơ chế (B1)**: `bulk_insert` INSERT→**UPSERT** theo key `(record_bot_id, workspace_id, lower(entity_name))` với `attributes_json = t.attributes_json || excluded` + `price_primary=COALESCE(excluded, t)`. Row Giá (xe-3) + row Kho/Mã (xe-1) cùng name → MERGE 1 entity đủ {Giá,Kho,Mã}. Alembic unique index hỗ trợ ON CONFLICT. Reconcile theo string-equality entity_name — KHÔNG domain vocab. **Guard**: chỉ merge disjoint attribute set (no overwrite field đã populated) — cần property test merge-conflict.
- **Cơ chế (B2)**: stats route **attribute-aware + non-destructive**: (1) khi resolve về SINGLE entity, chỉ surface field khớp attribute-class hỏi (price-ask→price); attribute hỏi VẮNG cột → empty → **refuse (HALLU=0)** thay vì substitute; (2) thêm `attribute_class` tag (price|stock|date|generic) vào parser theo signal-word generic; (3) chặn `list_all_entities` fire cho câu SPECIFIC-entity (chỉ fallback full-table khi shape 'liệt kê … nào').
- **Cơ chế (B3)**: thay hard short-circuit bằng `stats_index_race` (chạy stats+vector concurrent, union) HOẶC fall-through-to-vector khi keyword ILIKE degrade thành 100-row dump → prose value không bao giờ bị bypass. Đo p95 delta trước khi default ON.
- **n+1 proof**: bot N+1 upload catalog bất kỳ: (a) cùng thực thể ở N sheet → UPSERT-merge theo entity_name gộp attr, đúng mọi schema cột owner tự đặt; (b) câu 'attribute X of Y' mà X vắng → attribute-aware gate no-match → refuse, không fabricate; (c) prose value → race/fall-through để vector surface. KHÔNG nhánh if bot/ngành.

### NHÓM C — Purge/filter deleted + carry-header cross-chunk + (latent) ATTRIBUTE-index
- **Cơ chế (C1)**: inject stats_index_repo vào DeleteDocumentUseCase → `delete_by_document(doc.id)` sau archive; VÀ thêm `JOIN documents d ON d.id=si.record_document_id AND d.deleted_at IS NULL` vào MỌI serving query (list_all/query_by_name/price_range/top_by_price/count) làm defence-in-depth. Purge/filter theo deleted_at chung mọi tenant.
- **Cơ chế (C2/C3)**: (a) `tabular_markdown.open_header` KHÔNG fill col{i} ngay — nếu header có gap và row kế là continuation → MERGE 2 row tại converter (tái dùng `_is_header_continuation`/`_merge_header_rows` shape-logic), chỉ fill col{i} nếu vẫn rỗng; (b) `parse_table_chunks` thread per-document header/roles carry-over: data-only chunk kế thừa header+roles từ chunk trước cùng document → relabel col_N → nhãn corpus thật (Mã/Tồn/Ngày về/ảnh). Shape-based, không vocab.
- **Cơ chế (C4)**: CHƯA fix — capture live retrieve-trace per failing query trước (xác định code-route vs BM25 vs stats miss). **GIẢ THUYẾT, không ship mù.**
- **Cơ chế (C5, latent N+1)**: ADR-0007 PRICE-index→ATTRIBUTE-index flag-gated: numeric-attribute range/superlative/count over attributes_json theo label OWNER tự đặt ('price' chỉ là 1 label); thay parse_money_vn floor bằng locale-agnostic number capture (số nhỏ tồn 26/RAM 8 sống). Backward-compat VIEW cho price_primary. **TÁCH** khỏi carry-header+reconcile (multi-week, hard-to-reverse).
- **n+1 proof**: bot N+1 re-ingest/xóa doc → purge+filter đảm bảo chỉ corpus LIVE phục vụ; header 2-dòng/merged-cell → converter merge theo gap-shape; cột quantity/date → bind role generic theo token-affinity. Property-based canary `test_multibot_ingest_canary.py` với 25 random-header seed (header random unseen → green = đúng cho domain engine chưa từng thấy = bot N+1).

---

## 4. LÀM NHƯ NÀO — kế hoạch theo phase

> Mọi step: TDD failing-test FIRST (RED) → fix (GREEN) → verify số thật. KHÔNG tuyên bố "fixed/pass" khi chưa có output. Coverage≥0.95 gate trước ship. KHÔNG sửa sysprompt (sacred #10).

### PHASE P0 — 0 API cost, làm NGAY  **[T2-CostPerf / eval-infra]**
Mục tiêu: có thước đo ĐÚNG trước khi sửa engine (tránh đo bằng scorer sai).
1. **Commit scorer 2-path MỚI as-is + chạy live end-to-end xe+spa** để thay headline 70/72% (output scorer CŨ). File `scripts/retest_golden_generic.py`. change_type: commit+run. Test: `python scripts/retest_golden_generic.py <xe_golden> chinh-sach-xe web <ws>`; assert `results[0]` có key `overlap`.
2. **Add PRICE/NUMBER-CRITICAL guard** trong `run_one()`: gold có money/quantity → bắt buộc fact_hit. change_type: edit ok-rule. Test: `score('Giá 8.000.000','…10 triệu…')==FAIL` mọi truncation; `score('…8.000.000…','…8.000.000…')==PASS`.
3. **Add VALUE NORMALIZER** `canon_number()`. Test: `canon('8.000.000')==canon('8 triệu')==8000000`; `score(gold='8.000.000',ans='8 triệu')==PASS`.
4. **Add ACRONYM expansion từ custom_vocabulary** (read-only DB query theo bot_id). Test stub `{CSD:'chăm sóc da'}`: PASS via expansion; empty vocab no-regression.
5. **Emit 2-tier metric** `coverage_pct` + `wrong_number_fails`. Test: spa report chứa F7/F8 trong wrong_number list.
6. **Regression-lock cliff/CRAG KHÔNG đụng** (Q3/Q4 đã clear): add assertion `test_grade_skip_high_score` + `test_cliff_filter` (top_score 0.974 skip grade, n_dropped=0 @ max_gap 0.28). change_type: add test (no prod change).

→ Sau P0: có Coverage + Faithfulness-proxy số THẬT, biết chính xác bao nhiêu fail là nhóm A (scorer) vs B/C (engine).

### PHASE P1 — sửa CODE engine + cần RE-INGEST  **[T1-Smartness]**
Thứ tự bắt buộc: **converter+reconcile+header TRƯỚC → re-ingest → bật purge/filter SAU** (open_question: nếu bật purge trước re-ingest, live HTML entity bị mangle → bot refuse/coverage drop thay vì có entity sạch). **CHƯA verify sequencing bằng load-test → phải đo.**

1. **(RED)** `tests/unit/test_stats_index_repository.py -k deleted_doc_excluded`: seed entity thuộc doc deleted_at NOT NULL, assert serving KHÔNG trả.
2. **Header-merge tầng converter** `tabular_markdown.py` (`open_header` + lookahead merge gap/continuation). Test `test_tabular_markdown.py -k two_row_header_merge`; sau re-ingest grep 0 key `col5..col10`. **[T1]**
3. **Carry-header cross-chunk** `document_stats.py` `parse_table_chunks` (thread per-document header/roles, `_premerge_split_headers` document-scope). Test `test_multibot_ingest_canary.py -k split_chunk` (flip S1 xfail→strict) + `test_document_stats*` 0 regression. **[T1]**
4. **Cross-doc reconcile** alembic unique index `uq(record_bot_id,workspace_id,lower(entity_name))` + `bulk_insert`→UPSERT merge. File `stats_index_repository.py` + `alembic/versions/*`. Test `-k cross_sheet_merge` + `-k reconcile` (union attrs, no-overwrite guard). **[T1]**
5. **Attribute-aware route**: `query_range_parser.py` tag `attribute_class` (signal-word generic); `query_graph.py` `_do_stats_lookup` chỉ emit field khớp class + suppress `list_all_entities` cho specific-entity + return None→vector fall-through khi chỉ dump match; `retrieve.py` fall-through hybrid khi keyword degrade. Test: câu stock trên corpus price-only → return None → vector → **refuse**; câu price → chỉ field price. **[T1]**
6. **(latent N+1) quantity/date value-role** `document_stats.py` (`_QUANTITY_COL_TOKENS`/`_DATE_COL_TOKENS` frozenset, bind labelled attr, mirror price_primary). Test feed CSV 'Số lượng'/'Ngày về' → entity.attributes có nhãn quantity/date, KHÔNG col_N. **[T1]**
7. **Purge-on-delete** `delete_document.py` inject stats_index_repo + `delete_by_document` + wire bootstrap DI + JOIN deleted_at 5 serving method. Test `test_delete_document.py -k stats_purged`. **[T1]**
8. **VERIFY end-to-end**: re-ingest bot xe (live HTML) + spa → re-run golden (scorer P0). F3 stock→**refuse** (HALLU eliminated), F7 combo→8tr surface, F8 disambiguation improved; Fanpage/Tiktok vẫn refuse (data absent correct). Report Coverage + Faithfulness delta số THẬT. Grep self-verify 0 hardcode cột/bot trong diff. **KHÔNG claim fix khi chưa có output.**

### PHASE P2 — cần provider sống / multi-week  **[T2/T3]**
1. **C4 notation gap (xe SKU/hotline/Ngày về)**: capture live retrieve-trace per failing query TRƯỚC (code-route vs BM25 vs stats miss) → fix đúng branch. **GIẢ THUYẾT, không ship mù.** **[T1 khi đã có trace]**
2. **C5 ADR-0007 PRICE→ATTRIBUTE index** (hard-to-reverse, multi-week): flag-gated + backward-compat VIEW, KHÔNG bundle với P1. Test `test_domain_neutral_guard.py` price-coupling baseline phải GIẢM. **[T3-Refactor]**
3. **LLM-judge optional Port** cho legal/definitional prose (legal run 100% provider-error session này, chưa assess). Default OFF, KHÔNG là gate. **[T2]**
4. **DEFAULT_STATS_INDEX_RACE_ENABLED global vs per-bot**: đo p95 delta (race thêm concurrent vector call mọi stats query) trước khi default ON; cân nhắc chỉ gate case 'keyword degraded to dump'. **[T2]**

---

## 5. OPEN QUESTIONS / DATA-GAP (KHÔNG fix bằng code)
- **xe F3 '134 cái'**: literal `134` KHÔNG tồn tại bất kỳ chunk/attr nào (0 hits 155R12C+134); KHÔNG có cột Số-lượng trong bất kỳ xe CSV. → F3 vừa là golden-key error VỪA là HALLU thật (bot phải **refuse**, không phải trả số). Cần owner xác nhận source-of-truth / thêm cột Số lượng. **Data-layer, không phải bug reconcile.**
- spa Fanpage/Tiktok/facebook = 0 chunks → refuse ĐÚNG (data absent), KHÔNG phải bug.
- F11 reassign nhóm A (golden '3-5' sai vs corpus '1-2 buổi').
- UPSERT merge có thể GỘP NHẦM 2 thực thể khác trùng entity_name rút gọn → cần đo tỷ lệ false-merge corpus thật + guard merge-conflict trước ship.
- Scorer mới chạy offline trên gold truncate 70/160 char → % chỉ là band ±2, phải chạy LIVE + average ≥2 run (LLM non-determinism). **GIẢ THUYẾT cho mọi % estimate.**

---

*Anchor: tổng hợp 4 sub-analysis 2026-06-26. Mọi claim gắn nhãn SỰ THẬT/GIẢ THUYẾT. Chưa có load-test post-fix → KHÔNG tuyên bố % lift.*
