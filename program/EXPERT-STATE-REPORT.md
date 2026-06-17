# EXPERT STATE REPORT — Ragbot RAG + Application (hiện trạng đầy đủ)

> Báo cáo hợp nhất **11 audit Phase 2** (7 engine `P2-A…G` + 4 application `P2-H…K`), đều evidence `file:line`/psql/EXPLAIN/commit/web-source.
> Date 2026-06-10 · branch `fix-260604-action-slotmachine-dead-key` · anchor `7dd1f84` · alembic head **0195** (đã verify `alembic heads`; claim "0260" của P2-H là false-match từ date-prefix `20260610_...`).
> STANCE = **EVOLVE, không rewrite**. Nhãn: ✅ ĐÃ CHUẨN · 🕰 LỖI THỜI vs SOTA-2026 · ↔️ LỆCH (doc≠code≠DB≠plan) · 🐛 SAI/HOLE.
> Đây là "report to" anh yêu cầu: **đã soi gì + ra được gì** trên cả luồng RAG lẫn application. Plan + luồng làm ở file riêng `EXPERT-PLAN.md`.

---

## 0. TL;DR — 1 màn hình sự thật

**Ragbot là một hệ RAG multi-tenant đã expert về KHUNG, nhưng "dây chưa nối hết" — ở CẢ engine LẪN application.** Meta-pattern tự-đặt-tên của dự án — **"built-but-not-wired"** — được xác nhận xuyên suốt 11 domain: code tốt, viết xong, ship flag-OFF hoặc 0-callsite, chờ nối. Đây là tin TỐT cho chiến lược EVOLVE: phần lớn việc = **nối dây + recalibrate + migrate đúng chỗ**, không phải viết lại.

- **226 item** gán nhãn có evidence: ✅109 (48%) · 🐛54 (24%) · 🕰32 (14%) · ↔️31 (14%).
- **Engine** (7 domain, 136 item): khung 21-node/33-step CHUẨN, sacred answer-path clean, narrate-then-embed LIVE. Nợ = RLS inert (P0) + exactly-once drop + tie-order + chunk-misclassify.
- **Application** (4 domain, 90 item): **KHÔNG phải tương lai — đã build rất nhiều** (13 route admin, feedback table, GDPR erasure, hash-chain audit, analytics service). Nhưng vế "đọc/học/đóng-vòng" chết: feedback-loop CỤT, workspace RBAC/quota dead, ingest-quota orphan, API key plaintext.
- **3 P0** (chặn GA): RLS 100% inert · API keys plaintext · exactly-once message-drop.
- **1 sacred cần phân xử**: app append ~6KB rule platform vào sysprompt (sacred #10 tension) — **CONFIRMED code `sysprompt_assembler.py:126`**, Phase 3 ADR quyết governed-exception vs violation.

---

## 1. SCORECARD theo 6 TRỤC CHARTER (Definition of Done)

| Trục | Mục tiêu | Hiện trạng (evidence) | Điểm |
|---|---|---|---|
| **ĐÚNG** HALLU=0/faith≥0.95 | answer không bịa | Answer-path **CLEAN + 9 lock-test** (P2-E §5.1); HALLU=0 verified 85/91 (`7dd1f84`). **NHƯNG**: (1) app append 6KB rule platform vào sysprompt (🐛 sacred #10 tension, `sysprompt_assembler.py:126`); (2) grounding observe-only **cắt 5 câu** → tail-claim mù (P2-E 🐛-1); (3) nano-judge 0195 **chưa A/B** (P2-E Q19); (4) tie-order nondeterminism (P2-A/D) | 🟡 **tốt-có-điều-kiện** |
| **ĐỦ** recall≥0.9 coverage≥0.95 | corpus có đáp án → trả đúng | **Coverage KHÔNG đo live** — chỉ offline `GRADED_*`; refuse-rate gộp chung refuse-đúng-OOS với refuse-sai-silent-miss (P2-I 🐛). Chunk ceiling bị cap: `_is_table_line` misclassify ~163 chunk + Block-feed flatten giết L2/L6 (P2-B 🐛-A/B) | 🔴 **chưa đo được = chưa kiểm soát** |
| **AN TOÀN** RLS leak-test pass · 0 cross-tenant | DB enforce isolation | **RLS 100% INERT** (psql proof: bogus tenant → 21 rows, P2-C). **API keys PLAINTEXT** `api_keys.value_plain` (P2-J 🐛-KEY, encryption machinery tồn tại nhưng skip). Workspace RBAC/quota **dead** (`role_definitions` 0 rows, P2-H). Design expert, enforcement OFF | 🔴 **P0 — 3 lỗ** |
| **NHANH** p95 T1<1/T2<3/T3<15 | latency tier | HNSW fine ở scale hiện tại (EXPLAIN: exact-sort 560 rows, recall 100%, P2-D §4). **Nhưng**: ingest fairness=0 (1 stream + Semaphore(5), noisy-neighbor); **0 SLO-breach alerting** (P2-J — plumbing có, scheduler thiếu) | 🟡 **chưa có cảnh báo vi phạm** |
| **RẺ** cost/query per-tenant · cache≥30% | đo cost thật | Version-bust cache CHUẨN (P2-F §6.1). Per-step LLM cost **ĐÃ persist** request_steps (P2-G #4). **Nhưng**: ingest LLM cost = **$0 unledgered** (P2-G 🐛-8, tenant corpus lớn vô hình); 0 read-query sum cost; ingest = dominant-spend mù sổ | 🟡 **cost engine OK, ingest mù** |
| **KIỂM SOÁT** mọi quyết định có log+lý do | observability + governance | **TRỤC YẾU NHẤT.** Feedback-loop **CỤT** (ghi live, đọc/học chết — P2-I); analytics refuse-thô; eval **self-bias** (judge cùng model-family answer-gen, P2-I D13); 33-step instrument tốt nhưng **data-in-DB-no-reader**; config-drift 2 path (P2-G); validate guard chết (P2-G); math_lockdown dead-row + doc drift | 🔴 **observe nhiều, học/đóng-vòng = 0** |

**Tổng**: 2 trục 🔴-P0 (AN TOÀN, KIỂM SOÁT) · 1 trục 🔴 (ĐỦ chưa-đo) · 3 trục 🟡. **Không trục nào xanh hoàn toàn** — nhưng mọi đường tới xanh = EVOLVE (nối dây/recalibrate/migrate), không rewrite.

---

## 2. MA TRẬN 11 DOMAIN × NHÃN

### Engine (Phase 2 pair 1-3 + single — đã có `P2-GAPMAP.md`)
| Domain | ✅ | 🕰 | ↔️ | 🐛 | Headline |
|---|---|---|---|---|---|
| A pipeline orchestration | 18 | 2 | 7 | 3 | 21-node/33-step CHUẨN; tie-order OPEN |
| B chunking/AdapChunk | 11 | 5 | 5 | 4 | narrate LIVE; table-misclassify + Block-flatten |
| C multi-tenancy/security | 9 | 2 | 2 | 6 | 4-key SOTA; **RLS inert P0** |
| D retrieval/ranking | 6 | 7 | 1 | 2 | cliff/safety-net quý; tie-break + true-BM25 |
| E llm-ops/anti-hallu | 7 | 2 | 2 | 3 | sacred clean; grounding tail-blind |
| F data/cache/event | 7 | 2 | 2 | 9 | version-bust CHUẨN; **exactly-once drop** |
| G platform/config/cost | 5 | 2 | 1 | 4 | 5-tier resolve CHUẨN; ingest cost $0 |
| **Engine subtotal** | **63** | **22** | **20** | **31** | 136 item |

### Application (Phase 2 mở rộng — file `P2-H…K`)
| Domain | ✅ | 🕰 | ↔️ | 🐛 | Headline |
|---|---|---|---|---|---|
| **H** bot-owner control-plane | 7 | 2 | 3 | 6 | thin-controller + hash-audit CHUẨN; **sacred #10 sysprompt-append** + workspace RBAC/quota dead + ingest-quota orphan |
| **I** analytics/feedback-loop (D12) | 6 | 3 | 1 | 8 | analytics-service RLS-correct; **feedback-loop CỤT** (write live, read/learn 0-caller) + eval self-bias (D13) + coverage không observable |
| **J** ops/SLO/DR/PDPD (D11) | 22 | 3 | 3 | 7 | **layer chín nhất** — hash-audit + PII-redact + GDPR-erase GA-grade; **API key plaintext (HIGH)** + DR RPO≈24h + 0 SLO-alert + PDPD legal stale |
| **K** API/channel/schema | 11 | 2 | 4 | 2 | **no-version-ref PASS + 4-key PASS + header-versioning**; get_graph DI singleton order-dependent + 4 transport-divergence worker-vs-SSE |
| **App subtotal** | **46** | **10** | **11** | **23** | 90 item |
| **GRAND TOTAL** | **109** | **32** | **31** | **54** | **226 item** |

---

## 3. PHÁT HIỆN HỢP NHẤT — theo severity (cross-layer)

### 🔴 P0 — chặn GA (3)
1. **RLS 100% inert runtime** (engine C + G) — app connect `postgres` superuser (rolbypassrls=t); hook 0-callsite; `app.workspace_id` GUC never SET. psql bypass-proof: bogus tenant → **21 bot rows**. Isolation hiện chỉ dựa app-WHERE belt. → D3.
2. **API keys plaintext** (app J 🐛-KEY, HIGH) — provider key lưu `api_keys.value_plain`; alembic 0086 tự-ghi `value_encrypted` = "reserved, planned". AES-GCM machinery có (`env_secrets.py`) nhưng hot-swap skip. Cộng superuser DSN + pg_dump backup = cleartext key trong backup. → D11.
3. **Exactly-once = at-most-once** (engine F H-EO) — dedup `SET NX` TRƯỚC handler; handler raise → XCLAIM redeliver → dedup-skip + XACK = **message DROPPED**. Re-verified line-by-line. → D8b (new).

### 🟠 CRITICAL (4)
4. **Sacred #10 — app append 6KB rule platform vào sysprompt** (app H, **CONFIRMED**) — `sysprompt_assembler.py:126` `return base + platform_rules`; rule 15-19 từ `language_packs[locale].sysprompt_default_rules`, append SAU `bot.system_prompt`, per-bot opt-out qua `plan_limits`. Engine audit (P2-A/E) test ở `generate` node nhận prompt ĐÃ-lắp → thấy "verbatim", KHÔNG bắt được append upstream `chat_worker.py:1436`. **Owner KHÔNG viết rule 15-19 → owner-prompt KHÔNG còn single-source-of-truth.** Governed (alembic-seeded, domain-neutral-claimed, opt-out) nhưng vẫn là app-thêm-text. → **Phase 3 ADR phân xử**: approved-exception (Tier-6 platform-default hợp lệ như config) vs violation (phải gỡ).
5. **get_graph DI singleton order-dependent** (app K 🐛-K1) — `query_graph.py:8062` singleton bỏ qua kwargs sau build đầu; `chat_stream.py:243-264` thiếu 4 DI (`hyde_generator`/`understand_query_cache`/`stats_index_repo`/`doc_repo`). Nếu SSE build graph TRƯỚC worker → 4 deps = None **toàn platform**, non-deterministic theo warm-up order. CHƯA load-test verify = GIẢ THUYẾT nặng. → wire shared builder.
6. **Feedback-loop CỤT** (app I, cốt lõi D12) — vế GHI đủ+LIVE (`/feedback/thumbs`→`message_feedback` RLS-scoped). Vế ĐỌC/HỌC chết: `aggregate_per_bot` 0-caller, `FeedbackGiven` 0-subscriber, `FAQCandidateService` (refuse→FAQ) 0-callsite, `admin_refuse_suggestions` **không import trong router.py → 404 unreachable**. INSERT rồi nằm chết. → D12.
7. **Bot/tenant purge nothing + orphan family** (engine F) — soft-delete purge 0 downstream; semantic_cache no-FK; stuck-doc reaper mù `active`+0-chunk. Storage vô hạn. → D4.

### 🟡 HIGH (10)
8. **Tie-order nondeterminism** OPEN post-revert (A/D) — content-aware key, KHÔNG uuid → D5.
9. **Grounding ≤5-câu cap + silent-degrade vô hình** (E) — tail-claim mù + judge-chết = "grounded" thầm → D7.
10. **`_is_table_line` misclassify** (B) — ~163/211 chunk văn xuôi luật VN narrate-embed oan → D1.
11. **Block-feed flatten** (B 🐛-A) — `document_worker.py:295` giết L2/L6 AdapChunk engine → D1/D14.
12. **Workspace RBAC/quota doubly dead** (H/C) — `role_definitions` 0 rows; `quotas.workspace_id` cột tồn tại nhưng filter tenant-only → D2.
13. **Ingest LLM cost = $0** (G) — narrate/enrich hardcode `cost_usd=0.0`, no parent → D9.
14. **DR real RPO ≈ 24h** (J ↔️-DR) — chỉ `pg_dump` nightly local; WAL/PITR promised nhưng server-side absent → D11 (ops-side).
15. **Eval self-bias** (I D13) — judge `gpt-4.1-mini` cùng family answer-gen; gold-author không có evidence người-độc-lập → D13.
16. **Ingest-quota orphan** (H IQ-1) — `IngestQuotaService.check_and_increment` 0 prod-callsite (chỉ demo `test_chat.py`); fairness gate never runs trên upload thật → D8/D2.
17. **Coverage không observable live** (I) — analytics refuse-thô không tách OOS-đúng vs silent-miss; `RagasMetricAdapter` = stub đo hằng số → D12.

### 🟢 MEDIUM / GOVERNANCE (8)
18. Config-drift `init_system_config` ≠ alembic 0020 (max_tokens 2.3×, rerank_top_n 2×) (G) → D9.
19. `validate_constants.sh` dead guard (trỏ file đã xóa, exit 0) (G) → D9.
20. math_lockdown dead DB rows (0 reader) + doc 04-D drift (A/E) → D6-adj.
21. **PDPD legal target STALE** (J 🕰) — Nghị định 13/2023 đã bị thay bởi **Luật 91/2025/QH15 + Nghị định 356/2025, hiệu lực 2026-01-01**; charter + D11 còn ghi "Nghị định 13". → update D11.
22. Ingest thiếu success-webhook (K) — chat có HMAC+retry+rotation, ingest chỉ poll (bất đối xứng SOTA hybrid) → D-webhook.
23. RBAC 7 route hardcode level `60`/`80` trong khi `DEFAULT_*_LEVEL` constants tồn tại (H RB-1) zero-hardcode drift → D9-adj.
24. Charter stale: alembic 0195 vs thật 0260; ↔️ `_resource_ownership.py` ở `interfaces/http/` không phải `middlewares/`; STATE_SNAPSHOT "RLS active" vs psql inert → doc-fix.

---

## 4. ĐÃ CHUẨN — ĐỪNG ĐỤNG (hợp nhất 11 praise-list, đập = lỗi nặng nhất)

**Identity & sacred (engine):**
1. **4-key identity + JWT-only tenant + Redis 4-key registry** (C) — SOTA-shaped, anti-spoof, DB unique constraint.
2. **Sacred no-inject/no-override answer-path + 9 lock-test** (A/E) — `generate` verbatim, grounding warn-only. *(lưu ý: sacred #10 tension nằm ở UPSTREAM sysprompt-assembler, không phải ở generate — xem §3.4).*
3. **Narrate-then-embed dual-content** (B) — embed narration, answer raw, DB-verified 211/211.

**Retrieval & cache (engine):**
4. **Cliff filter + retrieval safety-net + CRAG score-mode + bot-filter-PRE exact-sort** (D) — vết-sẹo-production, EXPLAIN-verified.
5. **Version-stamped passive bust + semantic-cache 4-key-scope-before-cosine** (F/C) — purge-free, no cross-tenant leak.
6. **5-tier resolve + 33-step request_steps + per-step cost-capture** (G) — LaunchDarkly-class.

**Application (mới phát hiện — chín hơn dự kiến):**
7. **Hash-chained tamper-evident audit** (`audit_log_hasher` + `audit_verifier`, J) — bit-stable, GA-grade.
8. **Boundary PII-redaction** (J, claude-mem pattern đúng — metadata-only persist) + **GDPR erasure** RBAC-80 tenant-scoped.
9. **Fail-closed IP/source rate-limit + per-tenant deny-by-default CORS + honeypot + security-headers** (J) — đủ tầng phòng thủ.
10. **Header-based schema-versioning (`X-Schema-Version`) + no-version-ref PASS + 4-key API boundary + `extra="forbid"` anti-smuggle** (K) — REST 2026 đúng.
11. **Thin-controller + cross-tenant SELECT-guard + atomic UPDATE-WHERE ownership** (H) — control-plane isolation solid.
12. **`TenantAnalyticsService` RLS-correct + `message_feedback` schema chuẩn + DB-ground-truth-verify + 3-run flip-detection eval** (I) — nền tốt, chỉ thiếu vế đọc.

---

## 5. 3 CHỦ ĐỀ "EXPERT GAP" (khái quát hóa 54 🐛)

1. **ENFORCEMENT chưa bật** (security/isolation): RLS inert · workspace RBAC/quota dead · API key plaintext · ingest-quota orphan. *Design có, dây chưa cắm.* → Wave 1-2.
2. **VÒNG HỌC chưa đóng** (KIỂM SOÁT/ĐỦ): feedback write-only · coverage không đo · eval self-bias · analytics refuse-thô · cost ingest mù. *Quan-trắc có, không ai đọc/học.* → Wave 5-6.
3. **DÂY GIỮA 2 TRANSPORT + INGEST PIPELINE chưa nối** (correctness): get_graph DI singleton · GraphRAG worker-vs-SSE · exactly-once drop · Block-feed flatten · table-misclassify. *Engine có, luồng lệch.* → Wave 1,3,4.

> Cả 3 chủ đề = **"built-but-not-wired"**. Đây là lý do STANCE EVOLVE đúng: việc chính là **WIRE + HARDEN + RECALIBRATE + MIGRATE đúng chỗ**, rewrite chỉ 1-2 module (parser→Block adapter).

---
*EXPERT-STATE-REPORT hợp nhất 11 file P2-A…K. 0 src/alembic/tests chạm (chỉ đọc + verify sysprompt_assembler.py). program/ UNTRACKED — chờ user duyệt commit. Plan + luồng làm → `program/EXPERT-PLAN.md`.*
