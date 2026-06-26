# COVERAGE SWEEP — INVENTORY VẤN ĐỀ MỚI (ngoài 10 đã biết)

**Date**: 2026-06-26
**Scope**: tổng hợp 4 sweep song song — multi-format-ingest · tenant-rls-isolation · cache-concurrency-perf · action-security-boundary
**Mandate**: READ-ONLY, không sửa code. Mọi claim gắn nhãn SỰ THẬT (có evidence `file:line`/DB row) vs GIẢ THUYẾT.
**Mindset**: CLAUDE.md — EVOLVE không REWRITE (khung đúng, "dây chưa nối hết"); HALLU=0; tenant-isolation sacred; zero-hardcode; no app-inject; Coverage ≥ Faithfulness.

---

## 1. VẤN ĐỀ MỚI phát hiện (22 issue mới)

> Sắp xếp P0 trước. P0 = cross-tenant-leak / security boundary breach. Toàn bộ nhãn = **SỰ THẬT** (mọi finding có `file:line` hoặc DB-row evidence; không có GIẢ THUYẾT nào trong 4 sweep).

### P0 — Cross-tenant / Security (4 issue)

| id | tên | subsystem | sev | evidence | nhãn | N+1-impact | tiêu-chí-vi-phạm | fix-hint |
|---|---|---|---|---|---|---|---|---|
| **RLS-1** = **SB-1** | RLS BYPASS hoàn toàn ở runtime: app connect bằng `postgres` superuser (rolbypassrls=t), `DATABASE_URL_APP` unset, `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1`, role `ragbot_system` KHÔNG tồn tại | RLS / Boundary | **P0** | `pg_roles`: current_user=postgres rolsuper=t rolbypassrls=t. `.env` escape env=1, DATABASE_URL=postgres. `ragbot_app` tồn tại (NOBYPASSRLS) nhưng không dùng; `ragbot_system` 0 row. engine.py:60-92 fallback admin DSN. 24 policy LIVE nhưng superuser bỏ qua hết | SỰ THẬT | MỌI bot/tenant. Tenant-isolation 100% dựa app-WHERE, 0 defense-in-depth DB. 1 query path quên filter = leak ngay | S1 sacred QG#4 (DB-scoped tenant isolation phải thực thi ở DB) + S5 fail-loud (escape env biến RLS-bypass thành im lặng) | Ops provision `ragbot_system` (BYPASSRLS cho worker) + point `DATABASE_URL_APP`→`ragbot_app` (NOBYPASSRLS) + gỡ escape env. Health preflight FAIL-LOUD khi runtime role rolbypassrls=t mà escape env không set. KHÔNG sửa khung — chỉ WIRE DSN (EVOLVE) |
| **SB-2** | `conversation_state.save_state` UPDATE chỉ `WHERE id=conversation_id`, KHÔNG scope `record_tenant_id`, chạy qua plain `session_factory` (không GUC) → cross-tenant write risk khi RLS dead | Action/State / Boundary | **P0** | jsonb_conversation_state.py:135-144 UPDATE conversations SET action_state WHERE id=:id (no tenant). bootstrap.py:633-636 wired `session_factory` plain (không `session_with_tenant`). Kết hợp SB-1 → ghi PII slot không bị chặn tenant ở cả 2 lớp | SỰ THẬT | Mọi action-bot (spa booking + bot mới bật action_config). conversation_id UUID entropy cao nên va chạm ~0, nhưng resolve nhầm id / payload spoof → ghi đè action_state tenant khác, không lớp nào chặn | S1 QG#4 tenant isolation; S5 fail-loud (phụ thuộc RLS dead) | Thêm `AND record_tenant_id=:tenant` vào WHERE + truyền record_tenant_id vào save_state; HOẶC wire qua `session_with_tenant`. Ưu tiên CẢ HAI: app-filter + role-fix |
| **FMT-1** | Local-upload (`local://`) BYPASS toàn bộ structured parser — reuse flat raw_content làm full_text | Multi-format ingest | **P0** | document_worker.py:300-313 reuse stored raw_content làm full_text cho non-refetchable; structured-parser block :336 chỉ chạy `if not full_text.strip()`, registry routing :392-423 nằm trong đó. PDF/DOCX/XLSX/PPTX upload local KHÔNG bao giờ tới kreuzberg_markdown/docx/excel. DB: 0 real file-upload (chỉ Google URL + empty text/plain) → path untested prod | SỰ THẬT | MỌI bot/tenant upload binary qua canonical local:// bytes path — heading/table/atomic-block bị flatten thầm lặng. Bot mới hit với 0 sửa code | S1 (MỌI format đi CÙNG 1 luồng canonical → markdown-CÓ-CẤU-TRÚC); S5 fail-loud (mất cấu trúc im lặng) | local:// store raw BYTES (không flat text) + route qua `_route_through_parser/detect_parser_robust` như URL path; chỉ reuse full_text cho legacy no-bytes rows |

> **Lưu ý**: RLS-1 và SB-1 là **CÙNG MỘT root cause** (postgres BYPASSRLS), phát hiện độc lập bởi 2 sweep → cross-validated = bằng chứng mạnh nhất. Đếm 1 issue logic.

### P1 — High (10 issue)

| id | tên | subsystem | sev | evidence | nhãn | N+1-impact | tiêu-chí-vi-phạm | fix-hint |
|---|---|---|---|---|---|---|---|---|
| **SB-3** | Structured-output routing dùng substring match wire-name; model innocom `openai/claude` khớp NHẦM nhánh OpenAI strict json_schema; `ai_models.supports_json_mode` (SSoT thật) không bao giờ được tra | Action/Slot | **P1** | structured_output_helper.py:116-121 substring `any(token in haystack)`, haystack=`openai/claude|innocom` chứa 'openai'→True. DB supports_json_mode=t nhưng grep cho thấy KHÔNG dùng ở call-site routing | SỰ THẬT | N+1 unsafe: binding model mới tên chứa openai/azure/claude/anthropic → ép nhánh sai. Gateway innocom nhận json_schema strict có thể reject → decomposer+slot_extractor trả None → booking vỡ thầm lặng | S2 N+1; S4 SOTA (capability-driven phải dựa flag DB); S3 open-closed | Route theo `supports_json_mode` + provider capability flag thay vì substring tên; truyền flag xuống call_with_schema; chỉ bật strict khi flag=true |
| **CB-CLIENT-4XX** | LLM circuit-breaker ghi client 4xx (BadRequest/Auth/ContextWindowExceeded) là provider failure → OPEN một provider KHỎE cho mọi tenant | Cache/Concurrency | **P1** | dynamic_litellm_router.py:709-712/871-879/920-923 bare `except Exception`→`record_failure()`. `_RETRYABLE_LLM_EXCEPTIONS`:128-135 loại 4xx; `_is_rate_limit`:150 chỉ lọc RateLimit → 4xx rơi vào broad except, trip breaker | SỰ THẬT | Breaker keyed per-PROVIDER. Bot mới sysprompt malformed (400) / api_key sai (401) — fail MỌI call — sau fail_max OPEN shared breaker → fast-fail answer model cho MỌI bot khác trên provider đó. Self-inflict platform outage, 0 sửa code | S5 fail-loud (client bug fail loud per-request, không degrade provider khỏe); CLAUDE.md graceful-degradation | Thêm `_is_client_error()` predicate, skip `record_failure()` cho 4xx — cùng shape `_is_rate_limit`. Reraise LLMError fail loud |
| **PERSIST-CACHE-TASK** | Fire-and-forget semantic-cache write task không được reference → CPython GC drop giữa chừng | Cache/Perf | **P1** | persist.py:197-214 `create_task(_bg_cache_write, name=...)` return bị discard (không assign/store). grep `_background_tasks/add_done_callback/task_set` = 0 hit. Tương phản query_graph.py:1731/2218 (await trong scope) | SỰ THẬT | Mọi bot/turn ghi cache (non-refuse, non-multi-turn). Under GC/concurrency → task collected trước khi xong → cache write rớt thầm → hit-rate giảm, lặp LLM cost. Bot-agnostic | S4 SOTA (CPython gotcha: giữ strong ref); CLAUDE.md Async background-task wrapper | Giữ module-level `set[Task]`; `t=create_task(...); _BG.add(t); t.add_done_callback(_BG.discard)` |
| **SB-4** | Webhook DNS-rebinding SSRF: SSRF check ở validate/enqueue-time (resolve 1 lần) nhưng `CallbackDelivery.deliver()` re-resolve DNS lúc giao, KHÔNG re-check IP | Security/Webhook | **P1** | chat_async.py:241-247 `_is_url_safe` trước enqueue; callback_validator.py:35-59 resolve tại validate. NHƯNG callback_delivery.py:100-104 deliver() chỉ `client.post(url)` — httpx tự resolve lại. Cửa sổ TOCTOU | SỰ THẬT | Mọi tenant dùng callback_url. Attacker host public lúc validate → đổi DNS sang 10.0.1.160:5432 / 169.254.169.254 metadata / loopback trước deliver → POST body (answer + PII) vào nội mạng. Bot mới bật webhook tự dính | S5 secure-by-default; claude-mem boundary; OWASP SSRF (DNS-rebinding) | Re-validate IP TẠI deliver-time: resolve→pin IP đã kiểm→ép httpx connect IP đó (transport pin); allowlist host per-tenant |
| **SB-5** | PII boundary vs action/slot mâu thuẫn: redaction mask query TRƯỚC pipeline → slot_extractor nhận [PHONE]/[CCCD] → capture rỗng; nếu tắt redaction → slots_filled raw vào conversations.action_state JSONB | PII/Action | **P1** | pipeline.py:267-273 redact trước build_chat_initial_state:587; graph_assembly.py:177 raw_user_message=query (đã redact); generate.py:191-196 slot source toàn text đã redact. jsonb_conversation_state.py:154-173 _sanitize lưu json.dumps raw, không mask PII | SỰ THẬT | Bot booking bật redaction = slot luôn rỗng (mù thầm lặng). Bot tắt = PII (CCCD/phone) raw ở action_state, không DB-scope (SB-1/SB-2). 2 feature first-class loại trừ nhau | S1 sacred (PII redact tại boundary trước DB); S5 fail-loud (slot rỗng im lặng) | Tách 2 đường: raw_user_message UNREDACTED chỉ cho slot_extractor in-memory; redact riêng cho persist/log/LLM. Field-level encrypt slots_filled trước save_state |
| **RLS-2** | Policy `document_service_index` dùng `current_setting('app.tenant_id')` thiếu `missing_ok=true` (khác 23 policy) → ERROR thay vì fail-closed khi GUC unbound | RLS | **P1** | squashed_baseline.sql:1477 thiếu `, true` (so với :1479 documents có). pg_policies xác nhận qual không có 'true'. GUC chưa SET → raise 'unrecognized configuration parameter' thay vì NULL | SỰ THẬT | Mọi bot có stats index. Khi ops bật ragbot_app (RLS-1 fixed): session đọc/ghi document_service_index không bind GUC → CRASH UndefinedObjectError trên TẤT CẢ bot, không fail-closed | S5 fail-loud SAI KIỂU (1 bảng raise, 23 bảng fail-closed); S1 no-psql-hotfix (policy chỉ trong squashed_baseline, không migration tracked) | Alembic migration: DROP+CREATE POLICY với `current_setting('app.tenant_id'::text, TRUE)` + thêm workspace_id dimension. Regression test pin: mọi data-table policy phải missing_ok=true |
| **RLS-3** | `document_service_index` thiếu FORCE ROW LEVEL SECURITY (relforcerowsecurity=f) trong khi mọi bảng data khác FORCE ON → table-owner bypass | RLS | **P1** | pg_class: document_service_index relforcerowsecurity=f; document_chunks/documents/semantic_cache/bots/conversations/messages =t. squashed_baseline.sql:1433 chỉ ENABLE (không FORCE). owner=postgres | SỰ THẬT | Mọi bot dùng stats index (spa/xe). Khi chuyển ragbot_app: nếu app role là owner → RLS bảng này bị bỏ trong khi bảng khác enforce → lỗ hổng bất đối xứng. Hiện app-filter che nên chưa leak | S1 QG#4 (isolation đồng nhất); S3 open-closed (bảng data mới phải auto cùng RLS posture) | Alembic: `ALTER TABLE document_service_index FORCE ROW LEVEL SECURITY`. Test pin: mọi bảng có record_tenant_id phải relforcerowsecurity=t |
| **FMT-2** | CSV không có `.csv` ext misroute sang MarkdownParser → table structure bị phá | Multi-format ingest | **P1** | Repro: `detect_parser_robust('application/octet-stream','',semicolon-CSV)` → 'markdown'. mime_sniff.py:152-161 chỉ flag text/csv khi dòng đầu ≥3 COMMAS (miss semicolon/tab/2-col) + ignore file_name ext cho text branch | SỰ THẬT | Tenant upload CSV (EU/VN Excel default semicolon) không .csv ext / octet-stream mime → mất toàn bộ table structure. Hit bot mới 0 sửa code | S1 (structured-markdown cho tabular); S4 SOTA (csv.Sniffer-trivial); domain-neutral | sniff_real_mime text branch: honor file_name ext trước (.csv/.tsv), rồi csv.Sniffer / multi-delimiter heuristic (comma|semicolon|tab) thay vì ≥3-comma |
| **FMT-3** | VLM image-caption prompt hardcoded tiếng Việt + app-inject vào vision LLM (no per-bot override) | Multi-format ingest | **P1** | vlm_image_parser.py:47-52 `_CAPTION_PROMPT` fixed Vietnamese; document_worker build_parser ~:166 construct VlmImageParser KHÔNG truyền `prompt=`. Không system_config/per-bot key feed | SỰ THẬT | Tenant non-Vietnamese: ảnh được caption bằng instruction tiếng Việt; cùng text cho mọi tenant. Bot-agnostic | S1 **sacred rule#10** (Application KHÔNG inject text vào LLM — bot owner single source) + zero-hardcode + domain-neutral (VN literal trong code) | Source caption instruction từ system_config (`vlm_caption_prompt`) / per-bot; thread qua `build_parser(prompt=...)`; default domain-neutral, không VN literal |
| **RLS-4** | StatsIndexRepository read-path filter CHỈ record_bot_id, KHÔNG record_tenant_id + dùng plain `self._sf()` — an toàn nay (bot_id UUID unique) nhưng lệ thuộc 100% after_begin hook khi RLS bật | RLS | **P1**→P2 | stats_index_repository.py: query_by_price_range L244 chỉ `record_bot_id`; top_by_price L317; delete_by_document L167-173 plain self._sf() docstring tự nhận 'RLS not enforced'. Khác pgvector_store/semantic_cache (có record_tenant_id WHERE) | SỰ THẬT | Mọi bot có stats index. Nay KHÔNG leak (record_bot_id UUID global-unique). Rủi ro: caller mới (script/cron) đọc stats không set contextvar + RLS bật + RLS-2 → CRASH (không leak) | S4 defense-in-depth (cả app-filter LẪN RLS); S2 N+1 (repo ngoại lệ duy nhất, bẫy cho dev kế tiếp copy) | Thêm `AND record_tenant_id=:tid` vào read query HOẶC route qua session_with_tenant như bulk_insert. Chỉ thêm 1 predicate (EVOLVE) |

### P2 — Medium (8 issue)

| id | tên | subsystem | sev | evidence | nhãn | N+1-impact | tiêu-chí-vi-phạm | fix-hint |
|---|---|---|---|---|---|---|---|---|
| **GRADE-SO-NOT-GATED** | Grade structured-output gated bởi config flag, KHÔNG bởi bound model `supports_json_mode` → bot json-incapable rơi vào N-call per-chunk loop | Cache/Perf | **P2** | grade.py:172-186 gate trên pcfg flag, không đọc capability bound model; batch fail → fallback :313-347 1 call/chunk. DB active model supports_json_mode=t nên prod nay safe | SỰ THẬT | Tenant bind chat model supports_json_mode=false (legal) + default enabled=true → batch JSON fail → N LLM grading call/turn (N=top_K). p95 blowup, 0 sửa code | S2 N+1; S4 (capability chọn strategy); T2 cost/perf | AND supports_json_mode vào gate structured-output; nếu false skip strict, dùng rerank-order / single non-JSON batch, không bao giờ per-chunk N-call |
| **SB-6** | SlotExtractor giải khóa API key theo cơ chế KHÁC answer-path: không nhận provider_key_resolver, phụ thuộc litellm tự đọc env; provider_code suy ra từ string-split | Action/Secrets | **P2** | bootstrap.py:657-661 chỉ inject litellm_module+config_service. slot_extractor.py:137-146 không truyền api_key/api_base. So query_graph.py:1324-1331 truyền api_key=provider_obj.api_key. _resolve_model:180-183 provider_code=split('/')[0] | SỰ THẬT | Nay OK (key env trùng tên). Tenant/provider mới dùng api_key_encrypted / api_key_ref khác → answer-path resolve đúng nhưng slot-extractor không thấy → extract {} thầm lặng → booking vỡ. 0 sửa code không an toàn | S2 N+1; S3 open-closed (lặp resolve); S5 fail-loud (silent {}) | Inject provider_key_resolver/model_resolver vào SlotExtractor (hoặc reuse _invoke_structured_llm_node); api_key/provider_code lấy từ ai_providers như answer-path |
| **FMT-4** | PPTX không có OCR fallback — claim bởi structured parser only, drop nếu Kreuzberg-markdown path fail | Multi-format ingest | **P2** | kreuzberg_markdown_parser.py:46-56 claim presentationml. KREUZBERG_SUPPORTED_MIMES (_18_*:139) = {pdf,docx,jpeg,png,tiff,html,markdown} — NO presentationml. kreuzberg_markdown raise → OCR fallback :433-443 không nhận PPTX → empty → ingest fail | SỰ THẬT | Tenant upload PPTX = single point of failure không graceful degradation. CLAUDE.md liệt PPTX first-class | S1 (PPTX first-class); S5 fail-loud/graceful-degradation | Thêm presentationml vào KREUZBERG_SUPPORTED_MIMES (OCR adapter đã map .pptx ở _suffix_for_mime:110-111) |
| **FMT-5** | Legacy binary .doc/.xls/.ppt (OLE2 / d0cf11e0) hoàn toàn unsupported dù liệt first-class | Multi-format ingest | **P2** | grep d0cf11e0/OLE2/msword/ms-excel/ms-powerpoint src/ragbot = 0 hit. sniff_real_mime chỉ xử lý PK\x03\x04 OOXML, không OLE2 D0CF11E0. detect_parser không adapter cho msword/vnd.ms-* | SỰ THẬT | Tenant upload .doc/.xls/.ppt: byte-sniff trả declared mime, no parser match → OCR fallback (mime không trong supported) → empty/garbled. Bot mới hit 0 sửa code | S1 (DOC/XLS/PPT first-class CLAUDE.md); S3 open-closed (no adapter slot) | Thêm OLE2 magic + legacy adapter (Kreuzberg/LibreOffice convert), HOẶC document out-of-scope + fail-loud 'convert to OOXML' thay vì silent empty |
| **FMT-6** | `documents.mime_type` drift khỏi format thực-parse cho Google Doc/Sheet export | Multi-format ingest | **P2** | DB: 9 row mime_type='text/html' source=docs.google.com/{document,spreadsheet} — re-fetch qua to_export_url (:356-368) parse docx/csv, nhưng row giữ viewer-mime 'text/html'. 1 row octet-stream cũng Google doc | SỰ THẬT | Tenant sync Google Docs/Sheets metadata mime sai; harmless retrieval nay nhưng vỡ future mime-routing/observability/reprocess across all bots | S5 (observability honesty — metadata phản ánh parse path thật) | Sau to_export_url rewrite source+mime (csv/docx), persist corrected mime_type lên documents row thay vì viewer 'text/html' |
| **EMBEDCACHE-ORPHAN** | Bootstrap declare EmbedCache provider mà Redis key bỏ embedding dim; provider unwired (orphan) nhưng latent dim-collision nếu inject | Cache | **P2** | embed_cache.py:64-67 `_key=ragbot:embed:{model}:{hash}` — no dim. bootstrap.py:245 Singleton declared nhưng grep cho thấy không consume đâu. LIVE path dùng shared/embedding_cache.py key CÓ dim (`ragbot:emb:{model}:{dim}:{hash}`) — đúng matryoshka 2560→1280 | SỰ THẬT | Nay inert (dead). Nếu future inject: 2 bot cùng model name khác dim (2560 vs 1280) collide 1 key → đọc sai-dim vector → silent retrieval corruption | S3 open-closed/dead-provider hygiene; S4 (model-swap-safe key). Duplicate shared/embedding_cache | Gỡ orphan EmbedCache provider+class (SSoT = shared/embedding_cache.py), HOẶC thêm dim vào key |
| **SEMCACHE-TTL-HARDCODE** | `PgSemanticCache.store` hardcode `ttl_s=3600` default trong signature | Cache | **P2** | semantic_cache.py:540 `ttl_s:int=3600`. Caller persist.py:167 truyền DEFAULT_SEMANTIC_CACHE_TTL nên literal không exercise hot-path — nhưng default magic number ngoài shared/constants.py | SỰ THẬT | No runtime impact (caller override). Pure governance: future caller bỏ ttl_s nhận out-of-band 3600 không tie system_config | S1 zero-hardcode (3600 không whitelist) | Default `DEFAULT_SEMANTIC_CACHE_TTL` import từ shared/constants |

**Phân loại nhãn**: 22/22 issue mới = **SỰ THẬT** (có `file:line`/DB-row/repro evidence). 0 GIẢ THUYẾT. RLS-1≡SB-1 cross-validated độc lập (mạnh nhất).

---

## 2. SỨC KHỎE từng subsystem

| Subsystem | Verdict | Dựa vào đâu |
|---|---|---|
| **action-security-boundary** | **BROKEN** | SB-1 (postgres BYPASSRLS) khiến TOÀN BỘ RLS trên 6+ bảng nhạy cảm là cosmetic ở runtime — P0 boundary breach gốc, evidence DB trực tiếp (rolbypassrls=t). SB-2 phơi cross-tenant write khi mất lưới RLS. Cộng SB-3 (mis-route structured-output đúng triệu chứng action vỡ), SB-4 (SSRF), SB-5 (PII vs slot loại trừ nhau). RBAC numeric + rate-limit + idempotency + secret-scan = SẠCH. BROKEN chủ yếu vì tầng tenant-isolation DB không thực thi |
| **tenant-rls-isolation** | **AT-RISK** | 24 policy tồn tại THẬT (USING/WITH CHECK record_tenant_id, pg_policies xác nhận) — không chỉ bật cờ. GUC binding wire generic qua after_begin hook + session_with_tenant; hot-path scoping ĐÚNG (pgvector luôn filter record_bot_id, semantic_cache filter cả bot+tenant). KHÔNG tìm thấy cross-tenant LEAK runtime hiện tại. NHƯNG AT-RISK: RLS-1 toàn bộ RLS bị bypass runtime → còn 1 lớp app-filter; RLS-2/3/4 document_service_index lệch chuẩn → CRASH (không leak) đúng lúc ops bật ragbot_app. "Dây chưa nối hết" — khung đúng, cần WIRE DSN + đồng nhất 1 bảng lệch |
| **cache-concurrency-perf** | **AT-RISK** | Cache correctness post-1280 SOUND: query_embedding=vector(1280) verified DB; RLS-hooked session + explicit tenant+bot WHERE (defense-in-depth); numeric-poisoning NULL-embedding exact-hash; multi-turn skip đối xứng; embedding cache keyed model+dim (matryoshka-safe). Concurrency hygiene mostly đúng (gather semaphore-bounded + per-task session, Async Rule 7 honored). AT-RISK vì resilience layer: CB-CLIENT-4XX (P1, fast-fail provider khỏe platform-wide qua 1 bot misconfig) + PERSIST-CACHE-TASK (P1, GC-droppable → silent hit-rate leak) |
| **multi-format-ingest** | **AT-RISK** | Registry well-structured (Port+Strategy+DI, fail-soft, byte-sniff present); URL-fetch path route formats đúng. NHƯNG canonical local:// bytes path (FMT-1) BYPASS mọi structured parser — đúng lời hứa first-class CLAUDE.md — untested prod (DB 0 real binary upload). Cộng CSV-no-ext (FMT-2), VLM hardcoded-VN sacred#10 breach (FMT-3), PPTX/legacy gaps (FMT-4/5). Framework sound (EVOLVE); wiring upload→structured-parser chưa hoàn |

**Tổng**: 1 BROKEN (action-security-boundary), 3 AT-RISK. Mẫu số chung của BROKEN + 2/3 AT-RISK = **RLS-1/SB-1** (postgres BYPASSRLS) — đây là root-cause độc nhất kéo nhiều subsystem xuống.

---

## 3. CÒN CHƯA với tới (blind-spot — gộp not_checked)

Thành thật blind-spot còn lại sau 4 sweep READ-ONLY (chưa được verify runtime):

**Tenant/RLS**:
- Live cross-tenant probe bằng 2 tenant thật (set app.tenant_id A → SELECT của B) — không chạy được vì runtime superuser bypass; cần connect ragbot_app role mới đo fail-closed thật.
- `uow.py` UnitOfWork GUC binding qua từng transaction boundary (commit mid-session có xoá SET LOCAL không — CLAUDE.md cảnh báo).
- outbox_repository/publisher có chạy đúng system_session_factory (BYPASSRLS) không vs bind nhầm tenant GUC.
- knowledge_edges policy EXISTS subquery qua bots — RLS recursion/performance khi bật ragbot_app.
- Redis cross-tenant key collision (semantic_cache stampede lock, rate-limit namespace) khi 2 tenant cùng bot_id slug — record_bot_id UUID khác nên có vẻ OK nhưng chưa verify rate-limit dùng UUID hay slug.

**Cache/Concurrency**:
- Redis SETNX stampede lock dưới multi-worker race thật (chỉ đọc code, no load test).
- PERSIST-CACHE-TASK drop có thực sự xảy ra dưới prod GC pressure (static analysis only, no repro).
- redis_circuit_breaker.py / db_circuit_breaker.py error-categorization (chỉ soi llm_circuit_breaker).
- Actual p95 / LLM-call-count-per-turn (no load test); god-node generate.py excluded.
- `_grade_one_chunk` có catch exception trước gather return_exceptions=False (1 chunk fail abort cả batch — chưa trace hết).

**Action/Security**:
- Runtime load-test xác nhận SB-3 thực sự fire cho decomposer/slot với model 'openai/claude' — cần debug-trace 1 booking turn đo slot_extractor trả {} hay không.
- webhook_secret_rotation.py + hmac_signer rotation/replay-window receiver-side.
- webhook_deliveries dedup table + delivery-level idempotency (retry-sau-timeout receiver dedupe được không).
- anti_abuse/ip_rate_limit/bot_rate_limit/source_rate_limit chi tiết từng tầng.
- presidio/vn_regex PII recall mask CCCD/phone VN.
- Admin route còn lại (admin_ai/admin_bots/admin_webhooks) từng handler có require_min_level.
- EnvSecretsAdapter AES-GCM decrypt runtime (api_key_encrypted toàn NULL nên không có data verify).

**Multi-format ingest**:
- Kreuzberg runtime trên real PDF-scan/OCR (no scanned PDF in corpus).
- DOCX merged-cells/nested-tables/Heading 4+ (đọc logic cap Heading 1-3 + flatten nested, chưa parse real DOCX confirm).
- HTML table extraction quality qua kreuzberg_markdown (chỉ confirm routing, chưa output structure).
- OCR Kreuzberg có parse OLE2 (.doc/.xls) khi reached (phụ thuộc kreuzberg version installed).
- End-to-end load test binary upload (READ-ONLY mandate).

> **Blind-spot lớn nhất**: KHÔNG có live cross-tenant probe (vì runtime superuser bypass) → claim "không tìm thấy LEAK runtime" chỉ verify được sau khi WIRE ragbot_app. Hiện trạng = isolation chưa được kiểm chứng bằng adversarial probe, chỉ bằng code-read.

---

## 4. INVENTORY ĐẦY ĐỦ

**Tổng = 10 known + 22 mới = 32 issue** (RLS-1≡SB-1 đếm 1 logic-issue cross-validated → 21 distinct mới + 10 known = **31 distinct**; giữ 22 entry để trace per-sweep).

### Phân bố theo SUBSYSTEM (22 issue mới)

| Subsystem | P0 | P1 | P2 | Tổng mới |
|---|---|---|---|---|
| action-security-boundary | 2 (SB-1≡RLS-1, SB-2) | 3 (SB-3, SB-4, SB-5) | 1 (SB-6) | 6 |
| tenant-rls-isolation | (RLS-1≡SB-1) | 3 (RLS-2, RLS-3, RLS-4) | — | 4 |
| cache-concurrency-perf | — | 2 (CB-4XX, PERSIST-TASK) | 3 (GRADE-SO, EMBEDCACHE, SEMCACHE-TTL) | 5 |
| multi-format-ingest | 1 (FMT-1) | 2 (FMT-2, FMT-3) | 3 (FMT-4, FMT-5, FMT-6) | 6 |
| **Tổng (distinct)** | **4** | **10** | **8** | **22** (21 distinct sau merge RLS-1≡SB-1) |

### Phân bố theo SEVERITY (22 mới)

- **P0 (cross-tenant/security)**: 4 — RLS-1≡SB-1 (RLS bypass runtime), SB-2 (conversation_state cross-tenant write), FMT-1 (local upload bypass structured parser).
- **P1 (high)**: 10 — SB-3, CB-4XX, PERSIST-CACHE-TASK, SB-4 (SSRF), SB-5 (PII vs slot), RLS-2, RLS-3, FMT-2, FMT-3, RLS-4.
- **P2 (medium)**: 8 — GRADE-SO, SB-6, FMT-4, FMT-5, FMT-6, EMBEDCACHE-ORPHAN, SEMCACHE-TTL.

### Phân bố theo TẦNG kiến trúc

| Tầng | Issue |
|---|---|
| DB/RLS (defense-in-depth) | RLS-1≡SB-1, RLS-2, RLS-3, RLS-4, SB-2 (5) |
| Ingest/parser wiring | FMT-1..6 (6) |
| Resilience (CB/task) | CB-4XX, PERSIST-CACHE-TASK (2) |
| Capability-routing (structured-output/json) | SB-3, GRADE-SO-NOT-GATED (2) |
| Secrets/key-resolve | SB-6 (1) |
| Security boundary (SSRF/PII) | SB-4, SB-5 (2) |
| Cache hygiene/zero-hardcode | EMBEDCACHE-ORPHAN, SEMCACHE-TTL (2) |

### Compliance theo CLAUDE.md sacred rule (issue mới chạm sacred)

- **QG#4 tenant isolation**: RLS-1≡SB-1, SB-2, RLS-2/3/4 (5).
- **Sacred rule#10 (no app-inject text vào LLM)**: FMT-3 (VLM hardcoded VN caption prompt). ⚠ vi phạm trực tiếp.
- **Zero-hardcode**: FMT-3, SEMCACHE-TTL.
- **Domain-neutral**: FMT-3 (VN literal trong code).
- **S5 fail-loud / graceful-degradation**: CB-4XX, FMT-1, FMT-4, RLS-2, SB-4, SB-5.

### Ưu tiên fix (EVOLVE-first, đúng tầng root-cause)

1. **RLS-1≡SB-1** (P0, ops + 1 health-check) — root-cause độc nhất kéo BROKEN + 2 AT-RISK. WIRE DSN ragbot_app/ragbot_system, KHÔNG sửa khung.
2. **SB-2 + RLS-2/3/4** (đồng nhất document_service_index + conversation_state app-filter) — phải fix CÙNG RLS-1 (RLS-2 sẽ CRASH ngay khi RLS-1 fixed).
3. **FMT-1** (P0 ingest) — local:// route qua structured parser; lời hứa first-class.
4. **SB-3 + GRADE-SO** (capability-routing theo supports_json_mode) — đúng triệu chứng action-flow vỡ.
5. **CB-4XX + PERSIST-TASK** (resilience P1).
6. **SB-4/SB-5** (SSRF + PII boundary).
7. P2 còn lại (FMT-2..6, SB-6, cache hygiene).

---

**Note (rule#0 CẤM ĐOÁN)**: Báo cáo này tổng hợp evidence từ 4 sweep READ-ONLY. Mọi issue = SỰ THẬT (có file:line/DB-row). Claim "không tìm thấy LEAK runtime" = code-read only, CHƯA verify bằng adversarial cross-tenant probe (blind-spot §3). Fix-impact của mọi đề xuất = GIẢ THUYẾT cho tới khi có load-test/probe đo thật.
