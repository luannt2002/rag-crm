# DEEP MULTI-BOT ARCHITECTURE — Tổng hợp 4 phân tích (2026-06-26)

> Vai trò: RAG Master architect. Tổng hợp 4 findings (data-control-N+1 · fabrication-root · flow-chaos · qwen3-strategy) trả lời ĐÚNG 5 câu user + gốc-rễ-từ-mọi-góc-nhìn, theo mindset CLAUDE.md (rule#0 no-guess, sacred-10 no app-inject/override, domain-neutral, EVOLVE-không-REWRITE).
>
> Nhãn evidence: **SỰ THẬT** = có file:line / DB row / grep verify. **GIẢ THUYẾT** = chưa replay/query đối chiếu trong session này (read-only mandate chặn gọi model external).

---

## CÂU 1 — DATA CONTROL N+1 BOT (QUAN TRỌNG NHẤT)

### Verdict: **PARTIAL** — thêm bot data-khác CÓ phải sửa code không?

**KHÔNG cần sửa code KHI** owner khai `custom_vocabulary['column_roles']` (đường config-driven đã có, Tier-2 authoritative).
**PHẢI sửa code KHI** owner KHÔNG khai + header ngoài vocab built-in, HOẶC đụng 2 trục mà config không cứu được (số phi-giá < floor, split-header misalign).

### Config-driven gì (đường ĐÚNG, không sửa code)

- `custom_vocabulary['column_roles']` `{header_label: name|value|category|aliases|attribute}` — Tier-2 authoritative, fold free-form qua `_normalise_custom_roles` (`document_stats.py:392-482`). Owner khai role cho header BẤT KỲ ngành (RAM/Diện tích/Hoạt chất) **KHÔNG sửa code**.
- `custom_vocabulary['synonyms']` mở rộng match query.
- `declared_labels` cho phép header fully-custom vẫn được nhận là header.
- `analyze_table_headers` phát advisory log `ingest_data_quality` báo cột nào chưa bind role.

### Assume-structure gì (Tier-1 inference KHI owner KHÔNG khai — chỗ VỠ silently)

| # | Giả định cấu trúc | file:line |
|---|---|---|
| 1 | name = cột-text-đầu-tiên (positional) | `document_stats.py:574` `eligible_name = name is None and (name_idx is None or idx == name_idx)` + `:588` |
| 2 | `PRICE_MIN_VND=10000` floor cứng locale-VND → số nhỏ (tồn 404/26, m², số buổi) `parse_money_vn`→None → **giá trị BIẾN MẤT** | `constants DEFAULT_PRICE_MIN_VND=10000`; `document_stats.py:543-550` |
| 3 | vocab `_NAME/_CATEGORY/_PRICE_COL_TOKENS` hardcode literal VN+EN thương mại ('gia','price','kho','ten hang','thuong hieu','brand') | `document_stats.py:155-204` |
| 4 | header = 1 dòng; split-header chỉ vá 2 dòng + misalign khi leading-empty-col | `document_stats.py:766-795` + xfail canary `test_s1_split_header_labels_row2_columns` |
| 5 | money format single-locale VN (dotted-thousand, 'tr'/'k') | `document_stats.py:222-245` |
| 6 | schema cột chuyên dụng `price_primary/price_secondary` (PRICE-index, KHÔNG numeric-attribute generic) — 127 price-coupling token = "Betrayal #1" | `stats_index_repository.py:10-24`; `test_domain_neutral_guard.py:38-50` `_PRICE_COUPLING_BASELINE=127` |
| 7 | discourse-opener/char-cap guards hardcode VN grammar (`>120 char`, `>12 word`, 'hiện tại'/'hiện nay') | `document_stats.py:75-80, 632-645` |
| 8 | tie-break name⊥category bind NOTHING khi score bằng → warehouse-stub thành entity name | `document_stats.py:475-477` |

**DB proof (SỰ THẬT)**: cả 3 bot production (chinh-sach-xe, test-spa-id, thong-tu) **KHÔNG bot nào có key `column_roles`** trong `custom_vocabulary` → 100% dựa Tier-1 inference. Bot xe: 63/496 entity có key `col_N`, entity NAME='Kho lốp các loại' (warehouse stub thành tên), 325/496 price NULL.

### Cơ chế control-data-chuẩn-N-kiểu (fix proposal — T1, EVOLVE)

1. **ATTRIBUTE-index thay PRICE-index** (ADR-0007, item lớn nhất): cột `numeric_attrs JSONB {label:number}` + index; parse MỌI số (bỏ floor 10000 cho non-price; floor chỉ áp khi role=value). → root cho 325/496 NULL + "số tồn mất".
2. **Fix split-header leading-empty-col misalign**: normalize alignment sau `_premerge` → gỡ xfail canary S1.
3. **Surface data-quality report qua ingest API response** (đã có `analyze_table_headers`, mới chỉ log) → owner thấy `has_name_column=False` NGAY lúc upload.
4. **Fallback name-col robust**: 0-role-bind → chọn cột entropy/uniqueness cao nhất, ít số nhất (KHÔNG positional-blind col-0).
5. **Canary random-domain property-test**: assert price-floor không drop số nhỏ + multi-locale + 3-row header.

**Cơ chế domain-neutral**: engine biết ROLE (name/value/category/aliases/attribute) + STRUCTURE (number/label/locale từ language_pack), KHÔNG biết MEANING ('giá'/'tồn kho'). Owner khai `column_roles` + `numeric_attr_locale` per-bot. Effort ~3-4 ngày.

---

## CÂU 2 — FIX HARDCODE PROVIDER (P0): binding OpenAI dead

**SỰ THẬT**: 4 binding query/ingest-path còn trỏ OpenAI dead (provider code='openai', model gpt-4.1-nano/mini) → 429.
- `chinh-sach-xe | enrichment | gpt-4.1-nano | openai` (ingest-path, chặn ingest).
- `slot_extractor.py:168-179` default alias `haiku` → `_DEFAULT_MODEL_WIRE` (Anthropic dead) → structured judge_fn gọi model dead → exception → None → degrade nhưng MẤT grounding/slot-check. **(GIẢ THUYẾT** — cần check `system_config['slot_extractor_model']` value thật).

### Nguồn gpt-4.1-mini (memory): purpose-based binding

Theo memory `feedback_haiku_partial_only`: Haiku CHỈ cho decomposer + HyDE + ingest enrich (token nhỏ); **LLM answer + CRAG grader + grounding = gpt-4.1-mini**. Đây là intent purpose-based, đúng config-driven.

### Fix config-driven (KHÔNG sửa code, KHÔNG psql hotfix)

- Mọi swap provider = **UPDATE `bot_model_bindings` qua admin UI có audit_log** HOẶC alembic tracked — KHÔNG psql UPDATE thủ công (sacred #7).
- 4 binding dead → re-point sang innocom (provider đang sống) qua binding purpose-based: `understand_query/grading/generation/grounding/enrichment` → model_id còn sống.
- KHÔNG hardcode model name trong code; resolve qua `model_resolver` 3-tier (per-bot binding → system_config + ai_models → NullObject) — đảm bảo `_lookup_platform_default()` fallback (memory `feedback_resolver_must_fallback_system_config`).

**Effort**: P0 0-API — chỉ DML binding (admin UI/alembic) + preflight `/health/models`. ~1-2h.

---

## CÂU 3 — PHƯƠNG ÁN QWEN3 (yếu structured-output)

### Structured call-site critical (SỰ THẬT)

9 schema distinct / 11 call-site, tất cả qua 1 cửa `structured_output_helper.call_with_schema` (`:328`):
UnderstandOutput (`understand.py:200`), DecomposeOutput, GradeOutput/GradeBatchOutput, ReflectOutput, GenerateOutput, GroundingVerdictsOutput (`local_guardrail.py:567`), SlotSchema (`slot_extractor.py:137`).

**2 chỗ structured-fail MẤT CHẤT LƯỢNG (không degrade trong suốt)**:
1. `understand.py:282/296` — parse fail → KHÔNG condense query + intent về DEFAULT_FALLBACK → router/decompose kém.
2. `slot_extractor.py:156-157` — parse fail → `{}` → flow đặt lịch hỏng.
(Còn lại đã degrade-graceful: generate→free-form, grade→AMBIGUOUS, grounding→(0,0) skip).

### ROOT (SỰ THẬT): routing substring, KHÔNG đọc capability DB

`structured_output_helper.py:116-121` match SUBSTRING hardcode `OPENAI_STRUCTURED_OUTPUT_PROVIDER_CODES=('openai','azure','azure_ai')` (`_14_anti_abuse...py:170`). innocom wire='openai/claude' → chứa 'openai' → `_is_openai_compatible=True` → gửi `response_format={json_schema, strict:True}` (nhánh NGHIÊM NGẶT nhất `:389-426`). Nhiều endpoint qwen3 (vLLM/sglang) KHÔNG honor strict json_schema (chỉ json_object/guided) → trả free-form → `model_validate_json` fail → `_fallback_json_parse` scan brace.

**Cột DB `ai_models.supports_json_mode` (innocom=True) + `supports_tools` (=False) TỒN TẠI nhưng helper KHÔNG đọc** (grep=0 hit) — "dây chưa nối".

### Chiến lược config-driven (xếp ưu tiên, ĐO trước khi claim %)

- **[NGẮN — 0 code]** `system_config['structured_output_mode']` per-provider (strict|json_object|none) đọc từ `supports_json_mode`; innocom→json_object. ĐO load-test 3 bot golden parse-rate strict vs json_object trước khi chốt.
- **[TRUNG — nối dây]** routing-capability thay routing-substring: helper nhận `supports_json_schema/supports_tools` → Port structured-strategy (strict_json_schema | **json_object** | tool_choice | best_effort), registry theo capability. Thêm nhánh json_object cho qwen3.
- **[TÁCH MODEL]** binding purpose-based: structured-critical (understand/grounding/slot) → model mạnh json; free-form (generation factoid/chitchat) → qwen3. Swap = UPDATE binding.
- **[RETRY+REPAIR]** 1 retry bounded khi validate fail (re-ask "JSON trước sai schema X"), chỉ structured-critical. ĐO cost-delta.
- **[ĐƠN-GIẢN-SCHEMA]** UnderstandOutput: `condensed_query` optional default=query gốc → Extra-inputs không fail toàn bộ.

**N+1 cảnh báo**: provider mới tên KHÔNG chứa 'openai'/'azure'/'claude' (vd 'qwen3' thật, 'gemini', 'mistral') → rơi nhánh 'Unknown provider' (`:489`) = KHÔNG enforcement. innocom "may mắn" hoạt động vì wire vô tình chứa 'openai' = **FRAGILE**.

---

## CÂU 4 — FIX BỊA (HALLU URL namphat.vn)

### Tầng gốc CHÍNH + % + evidence

| Tầng | % | Evidence | Nhãn |
|---|---|---|---|
| **INGEST/INDEX (CHÍNH)** | ~55% | cột image-link mangle: 0/496 entity có key 'image'/'ảnh'; chỉ 2/496 carry URL, cả 2 entity_name=TÊN KHO (header column). `document_stats.py:155-181` KHÔNG có role 'image'/'link' → URL rơi `attributes[col_N]` rồi data-row ảnh rỗng → entity mất URL | SỰ THẬT |
| **SYSPROMPT (phụ)** | ~30% | `bots.system_prompt` xe CÓ anti-fabricate GIÁ/TỒN/NGÀY nhưng **0 dòng cấm bịa URL/LINK/ẢNH**. 'image: link ảnh sản phẩm' chỉ mô tả cách đọc cột | SỰ THẬT |
| **STATS-INDEX SERVING (phụ)** | ~15% | `query_graph.py:2344-2346` `_is_field_like` cắt value >120 char (`DEFAULT_STATS_ATTR_MAX_CHARS=120`) → URL-list-cell bị strip | SỰ THẬT |

**KHÔNG phải tầng gốc**: DOC INPUT — link THẬT (drive.google.com) CÓ trong 96/222 chunk; 'namphat'=0 chunk → fabricate thuần, không mất-ở-input (SỰ THẬT). GENERATE node KHÔNG inject text URL (`generate.py:601`). OUTPUT GUARDRAIL KHÔNG check 'URL phải có literal trong chunk'.

**GIẢ THUYẾT** (chưa replay, read-only): entity spec-named ('Lốp 195/65R15') KHÔNG carry URL → synthetic chunk gửi LLM không có ảnh → LLM chế. Cần load-test 'cho xem ảnh lốp 195/65R15' bypass_cache + dump `<documents>` confirm chunks_used không chứa drive.google.com.

### Fix tận gốc N bot (đa tầng, sacred #2 KHÔNG override answer)

- **SHORT (0-API, chặn ngay mọi bot)**: append rule domain-neutral vào `language_packs[locale].sysprompt_default_rules` (qua SysPromptAssembler ADR-W1-S10, alembic tracked) đối xứng anti-fabricate-số: "CHỈ trả URL có LITERAL trong `<documents>`; thiếu → KHÔNG tạo URL, xin liên hệ." + per-bot xe thêm 1 dòng (admin UI, KHÔNG psql).
- **MID (ingest gốc)**: thêm role `media/attachment` first-class (`_MEDIA_COL_TOKENS`) + map qua `custom_roles` JSONB; cell media giữ key 'image' (không col_N), renderer miễn cap-120 cho role media. Re-ingest corpus xe.
- **LONG (governance)**: output guardrail opt-in `url_must_be_grounded` (extract http(s) URL, substring-match chunk; flag observability hoặc block per-bot config — KHÔNG override mặc định, tôn trọng sacred #2).

**Effort** ~10-14h. BẮT BUỘC load-test backward-verify TRƯỚC khi tuyên bố fixed.

---

## CÂU 5 — CODE-FLOW LOẠN?

### Số thật (SỰ THẬT, grep verify)

- **21 node** đăng ký, **12 static edge**, **9 conditional-edge** (router). 1 entry (`guard_input`) + END.
- Topology KHÔNG bùng nổ: 9 router hội tụ về `retrieve→generate→guard_output→persist→END`. 2 vòng lặp CÓ cap (grade→rewrite_retry `max_grade_retries`; reflect→generate `max_total_graph_iterations`).
- **120 config-key** đọc qua `_pcfg`; chỉ ~10 fork TOPOLOGY, còn lại tune IN-NODE (identity khi off). cascade/self-RAG/multi-query/cliff/HyDE/CR = IN-NODE; chỉ decompose/adaptive_decompose = topology-fork thật.
- **32 step_name** instrumented (memory '15/27 NOT_INSTRUMENTED' = OUTDATED).

### Dead / trùng (SỰ THẬT)

- **DEAD #1**: `condense_question` UNREACHABLE — `merge_condense_router` default code=True, KHÔNG seed alembic/constants (grep=0). node + edge condense→router dead path mặc định.
- **TRÙNG #2**: 2 decomposer song song — `decompose` (legacy) + `adaptive_decompose` (Adaptive L3 wrap `query_decomposer.py`) — cả hai →retrieve. 3 module cho 1 việc.
- **TRÙNG #3**: 2 parallel-wrapper bọc node legacy (cache_check_and_understand_parallel, rewrite_and_mq_parallel) — có chủ đích nhưng tăng tải đọc.
- **GOD-NODE**: `retrieve.py` 96KB/2110 dòng gộp 8+ sub-flow (multi_query/rrf/fallback/multistage/speculative/metadata-3-layer/parent_child/autocut/neighbor) — vi phạm single-responsibility.

### Cắt/gộp (T2/T3, GIỮ KHUNG)

1. **(T2, ngay)** Gỡ dead `condense_question` (hoặc seed flag) — giảm 1 node/1 edge/1 nhánh. ~1-2h.
2. **(T1-risk)** Gộp 2 decomposer qua flag canonical — CHỈ sau load-test đối chiếu coverage. ~3-4h.
3. **(T3, DEFER tới khi T1 đạt)** Tách god-node retrieve.py thành `retrieve_stages/` helper (KHÔNG thêm graph-node, giữ topology). ~6-8h.
4. **(T2, ngay)** Config-inventory test: assert mỗi `_pcfg` key có default + doc-row (chống config-drift — gốc 'loạn config'). ~2h.

**Verdict câu 5**: KHÔNG loạn GRAPH (topology chuẩn SOTA adaptive-RAG). 'Loạn' = CONFIG SURFACE (120 key, GIẢ THUYẾT 2^N behavior-variant) + god-node retrieve. N+1 bot: **NO code change** — graph singleton dùng chung mọi tenant (`query_graph.py:2826-2828`), 9 router chỉ đọc `_pcfg` config-chain.

---

## GỐC RỄ TỪ MỌI GÓC NHÌN → 1 ROOT SÂU NHẤT

| Góc nhìn | Triệu chứng | Gốc cục bộ |
|---|---|---|
| **Provider** | innocom strict-json fail, 4 binding OpenAI dead | routing SUBSTRING tên model thay vì đọc cột capability `supports_json_mode` ĐÃ CÓ DB |
| **Data** | entity name sai (Kho lốp), 325/496 price NULL, số tồn mất | Tier-1 inference hardcode vocab thương mại VN + PRICE-index thay ATTRIBUTE-index; owner KHÔNG khai `column_roles` |
| **Sysprompt** | bịa URL namphat.vn | anti-fabricate liệt-kê-cứng (giá/tồn/ngày) KHÔNG generic → URL ngoài phạm vi |
| **Test** | bug lọt | canary chỉ enumerate ngành đã biết, xfail S1; KHÔNG property-test random-domain/random-header |
| **Kiến trúc** | retrieve god-node, dead path, config 2^N | feature thêm bằng FLAG + IN-NODE branch, dồn complexity vào file thay vì port/stage tách |

### ROOT SÂU NHẤT (1 câu)

> **Engine đang nhúng GIẢ ĐỊNH MIỀN (domain meaning) vào tầng inference/routing/guard thay vì chỉ giữ STRUCTURE + ROLE và để owner khai MEANING qua config — cộng với việc "dây config-driven đã có nhưng chưa nối" (capability DB, column_roles, attribute-index, url-grounding).**

Mọi triệu chứng là một biểu hiện của cùng root: hệ thống hoạt động hoàn hảo cho **happy-case ngành thương mại VN có header khớp vocab built-in + provider tên chứa 'openai'**, và **VỠ SILENTLY** ngay khi data/provider/locale lệch khỏi happy-case MÀ owner không khai config bù. Đây KHÔNG phải "khung sai" (Hexagonal/Port/DI/4-key/sacred đều chuẩn) — đúng như CLAUDE.md strangler-fig: **"dây chưa nối hết", KHÔNG phải "khung sai"**. Fix = NỐI DÂY (capability-routing, attribute-index, column_roles surface, url-grounding, sysprompt generic), KHÔNG REWRITE.

---

## TÓM TẮT — THỨ TỰ FIX

### P0 — 0-API (config/DML, làm ngay, ~3-5h)
- **(C2)** Re-point 4 binding OpenAI dead → innocom qua admin UI/alembic (audit_log, KHÔNG psql). **T1**.
- **(C4-short)** Append rule anti-fabricate-URL vào `language_packs.sysprompt_default_rules` (alembic) + per-bot xe 1 dòng. **T1**.
- **(C3-short)** `structured_output_mode` per-provider đọc `supports_json_mode`; innocom→json_object — sau khi ĐO parse-rate strict vs json_object. **T1**.
- **(C5)** Gỡ dead `condense_question` + config-inventory test. **T2**.

### P1 — RE-INGEST (code ingest + rebuild index, ~1-2 ngày)
- **(C1)** ATTRIBUTE-index thay PRICE-index (ADR-0007) + fix split-header align + fallback name-col robust + surface data-quality report. **T1**.
- **(C4-mid)** role `media/attachment` first-class + re-ingest corpus xe (key 'image' thay col_N). **T1**.
- **(C1)** Khuyến nghị owner 3 bot khai `custom_vocabulary['column_roles']`. **T1**.

### P2 — ARCH (refactor/governance, DEFER tới khi T1 coverage ≥95%)
- **(C3-mid)** routing-capability + Port structured-strategy (json_object branch). **T2**.
- **(C5)** Gộp 2 decomposer (sau load-test) + tách god-node retrieve.py thành stages. **T2/T3**.
- **(C4-long)** output guardrail opt-in `url_must_be_grounded` (KHÔNG override mặc định). **T2**.

**Phân tầng**: P0/P1 = **T1-Smartness** (coverage + HALLU) — ưu tiên tuyệt đối. P2 = **T2-CostPerf/T3-Refactor** — defer cho tới khi T1 đạt (golden xe hiện 78% < 95% gate).

**BẮT BUỘC (rule#0)**: mọi claim %/fixed PHẢI load-test backward-verify (ingest→retrieve→topK→prompt→answer) + RAGAS/eval số thật TRƯỚC commit. Báo cáo này là **diagnosis tĩnh** (code-evidence) — CHƯA VERIFIED runtime; các GIẢ THUYẾT (stale-entity 87%, replay HALLU URL, parse-rate qwen3, endpoint honor strict) còn mở.
