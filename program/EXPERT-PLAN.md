# EXPERT PLAN — Ragbot RAG + Application (giải pháp + luồng làm)

> Plan biến hiện trạng (`EXPERT-STATE-REPORT.md`, 226 item / 54 🐛) thành "expert RAG + expert application" theo 6-trục DoD charter.
> STANCE = **EVOLVE, không rewrite**. Nguồn: 11 audit `P2-A…K` + `P2-GAPMAP` + decision register D1–D17.
> Date 2026-06-10. **Đây là PLAN DRAFT — chờ user approve từng GATE. KHÔNG tự trôi.** Mọi code = Phase 4 (sau ADR duyệt).

---

## 0. NGUYÊN TẮC (binding)

1. **EVOLVE**: WIRE + HARDEN + RECALIBRATE + MIGRATE đúng chỗ. Rewrite chỉ **1 module** = parser→Block adapter (charter-blessed). Đập cái ✅ = lỗi nặng nhất.
2. **Gate discipline**: 5 gate cần user approve. Đang ở **GATE 2** (GAPMAP+report). Phase 3 (ADR) → GATE 3. Mỗi wave Phase 4 → gate riêng. Phase 5 eval → GATE 5.
3. **No-guess must-measure** (rule #0): mỗi fix PHẢI có số đo trước/sau (load-test/RAGAS/EXPLAIN/leak-test/psql). Cấm claim % lift khi chưa đo.
4. **Sacred giữ nguyên**: HALLU=0 · 4-key · no-app-override-answer · zero-hardcode · no-psql-hotfix · domain-neutral · no-version-ref.
5. **Đo bằng trục**: mỗi wave tuyên bố kéo trục nào + metric gate cụ thể. Wave không kéo trục nào đo được = defer.

---

## 1. ROADMAP 6 WAVE (sắp theo phụ-thuộc + severity, không phải effort)

| Wave | Tên | Trục kéo | Decisions | Gate metric | Effort |
|---|---|---|---|---|---|
| **W1** | **STOP-THE-BLEED** (P0 security + data-loss + correctness-latent) | AN TOÀN, ĐÚNG | D3, D8b, D4, API-key-encrypt, get_graph-DI | leak-test 2-tenant 0-row (ragbot_app role) · exactly-once test handler.call==2 · 0 plaintext key · stream≡worker DI | **L** (~1 tuần) |
| **W2** | **MULTI-TENANT ENTITY** (workspace thật + fairness) | AN TOÀN, NHANH | D2, D8 | workspace leak-test 0-row · ingest-quota gate fires · per-tenant ingest-p95 fair | M-L |
| **W3** | **ADAPCHUNK HOÀN THIỆN** (chunk quality ceiling) | ĐỦ | D1, D14, D15, D16, D17 | coverage↑ on 91Q (A/B) · 0 prose mis-narrated · HALLU=0 hold | L |
| **W4** | **RETRIEVAL + ANSWER DETERMINISM** | ĐÚNG, ĐỦ | D5, D6, D7, D-trueBM25 | flip-rate↓ + ≥85/91 hold · grounding tail-covered · HALLU=0 | M-L |
| **W5** | **COST + CONFIG GOVERNANCE + EMBED-GUARD** | RẺ, KIỂM SOÁT | D9, D10 | per-(tenant,purpose) cost queryable incl. ingest · 4-way config-lint green · embed-change blocked | M |
| **W6** | **APPLICATION EXPERT** (feedback-loop + ops + compliance) | KIỂM SOÁT, AN TOÀN | D11, D12, D13, D-webhook | feedback→eval loop closed · SLO-breach alert fires · PDPL 91/2025 erase+export+consent · independent ground-truth | L |

**Phase 3 (ADR) chạy TRƯỚC mỗi wave** cho decisions của wave đó. Phase 5 (eval+ablation) chạy SAU mỗi wave + 1 vòng tổng cuối.

---

## 2. CHI TIẾT TỪNG WAVE (issue → action, evidence ở report)

### W1 — STOP-THE-BLEED (charter "code sửa ĐẦU TIÊN")
> 3 P0 + 2 correctness-latent. Không feature, chỉ bịt máu.
> **STATUS 2026-06-10: ✅ CODE 6/6 SHIPPED** (commits `1b06a46`/`18a31f5`/`c2bf270`/`83fee63`/`6121de8`/`e85f2b8`).
> Full suite 5784→5868 pass, 0 regression. Soak D8b: applied=500 dropped=0 double_apply=0.
> **Gate runtime CHỜ OPS** (`waves/W1-OPS-CHECKLIST.md`): set KEK → `alembic upgrade` 0196-0198 · set `DATABASE_URL_APP=ragbot_app` → leak-test + graded 91Q HALLU=0. Code-side gate = ĐÓNG; runtime gate = ops boundary.

| Item | Action (EVOLVE) | Evidence | Đo gate |
|---|---|---|---|
| **D3 RLS** | (a) attach `attach_rls_session_hook` 1 lần ở `bootstrap.py:162` (no-op khi unbound, an toàn land trước); (b) add `workspace_id_ctx` contextvar + `SET LOCAL app.workspace_id` ở `engine.py:143` + `session.py:110`; (c) ops set `DATABASE_URL_APP=ragbot_app` DSN (rollback ADR); (d) leak-test CI **assert `rolbypassrls=false`** (chống green-vacuous) | P2-C RLS-1/2/3 | 2-tenant + 2-workspace, connect `ragbot_app`: cross-tenant/cross-ws SELECT = **0 row**; test FAIL nếu chạy as superuser |
| **D8b exactly-once** | move dedup-mark Redis-`SET NX`-before-handler → Postgres `inbox(msg_id PK)` trong CÙNG tx handler; XACK sau commit; Redis NX giữ làm fast-path | P2-F H-EO §4 | `test_eventbus_exactly_once`: handler.call_count==2 (re-run sau fail), XPENDING==0 sau success |
| **D4 purge/reaper** | `BotLifecycleService.purge` (saga idempotent: chunks→cache→corpus_version→registry→hard-delete); extend reaper predicate `active`+0-chunk; add `semantic_cache` FK→bots ON DELETE CASCADE | P2-F H-BOT/TEN/FK/REAP §5 | seed→delete→assert 0 orphan {chunks,cache,corpus_key}; reaper trả active-0-chunk row |
| **API-key encrypt (P0)** | route hot-swap qua AES-GCM `env_secrets.py` đã có → ghi `value_encrypted`, ngừng `value_plain`; backfill migration | P2-J 🐛-KEY (alembic 0086) | psql: 0 row `value_plain IS NOT NULL` sau backfill; decrypt round-trip test |
| **get_graph DI (latent)** | shared `build_initial_state()` + DI container cho cả `chat_worker` + `chat_stream`; ép singleton nhận đủ kwargs hoặc fail-loud nếu thiếu | P2-K 🐛-K1 | unit: stream-state ≡ worker-state DI keys; assert 4 deps không None |

**Gate W1**: 4 leak/exactly-once/key/DI test xanh + HALLU=0 hold + 0 regression (674 test).

### W2 — MULTI-TENANT ENTITY
| Item | Action | Evidence | Gate |
|---|---|---|---|
| **D2 workspace→entity** | add `workspaces(id,record_tenant_id FK,slug,...)` + optional `workspace_members(workspace_id,user_id,role)`; backfill `workspace_id ← str(record_tenant_id)` (0062 đã có); RBAC ws-scope (seed `role_definitions` — hiện 0 rows) hoặc chốt "global-per-tenant là GA-model" + drop unused `scope` | P2-C Q6/Q7 + P2-H WS-1/2 | intra-tenant 2-ws leak-test 0-row; RBAC ws-scope enforce hoặc decision-doc |
| **D8 ingest fairness** | per-tenant token-bucket thay `Semaphore(5)` global; **wire `IngestQuotaService` vào `documents.py`+`documents_stream_upload.py`** (hiện orphan) | P2-C ingest + P2-H IQ-1 | quota gate fires trên upload thật; tenant-B p95 không degrade N× khi tenant-A load 100 doc |

**Gate W2**: workspace leak-test + ingest-fairness + quota-wired.

### W3 — ADAPCHUNK HOÀN THIỆN (verdict §3 GAPMAP)
| Item | Action | Evidence | Gate |
|---|---|---|---|
| **D1 fix-tại-chỗ (ship NGAY)** | `_is_table_line` comma-rule (≥2 dòng CSV liên tiếp / loại VN điểm-khoản); narrate CHỈ TABLE/FORMULA/IMAGE thật; proposition connector-retention + `source_sentence` metadata | P2-B 🐛-B/C/D | repro T1/T2/T3 xanh; DB: prose-as-TABLE = 0 |
| **D1/D14 Block-feed** | un-flatten `document_worker.py:295`→`ingest(blocks=)`→`smart_chunk_atomic` survivor; bỏ elif-ladder trùng; **rewrite cục bộ parser→Block adapter** (chỗ duy nhất rewrite) | P2-B 🐛-A Q9/Q12 | block-type từ parser truth; A/B coverage 91Q |
| **D16 large-table** | hợp nhất `_emit_table_rows(header,rows,chunk_size)`: atomic-ở-HÀNG, header-travel, FORMULA/IMAGE atomic-tuyệt-đối | P2-B Q15 | table-split test; spa-07 hold |
| **D15 proposition / D17 lifecycle** | LLM-prop (nếu wire) BẮT BUỘC entailment-gate; reaper DRAFT-unify (ingest INSERT `DRAFT`→flip `active` tại :3682) gộp 2 vocabulary | P2-B Q13 + P2-F Q24 | entailment-gate test; reaper 1-vocabulary |
| **semantic delete** | `_chunk_semantic_embed` + SentenceSimilarityPort → DELETE (sau ablation xác nhận) | P2-B 🕰-1 (arXiv 2410.13070) | ablation Phase 5 trước khi xóa |
| **Ekimetrics** | wire-for-ablation có **kill-date**: real config-key ở `select_strategy`; A/B 13 GRADED_*; 0 lift → xóa ~600 dòng | P2-B §5 | A/B verdict Phase 5 |

**Gate W3**: coverage↑ A/B + 0 prose-mis-narrate + HALLU=0 hold.

### W4 — RETRIEVAL + ANSWER DETERMINISM
| Item | Action | Evidence | Gate |
|---|---|---|---|
| **D5 tie-break + temp-0** | content-aware key `rerank_score→bm25_rank→chunk_index` (KHÔNG uuid); đẩy temp-0 override vào `complete_runtime` (bao 3 direct-call bypass) + per-stage `gold_chunk_in_set` instrument | P2-A 🐛-1 + P2-D §3 + P2-E 🐛-2 | flip-rate↓ + ≥85/91 hold (A/B 5-run) |
| **D7 grounding** | claim-level judge bỏ 5-câu-cap (swap qua Port `structured_judge_fn`→MiniCheck/DeBERTa adapter, ADR); add `grounding_degraded_total` counter | P2-E 🐛-1/3 🕰-A | tail-claim covered; degraded distinguishable |
| **D6 numeric** | (nếu cần) calculator-tool-node TRƯỚC generate, output vào USER-turn context như derived-fact có provenance — **KHÔNG override post-hoc**, KHÔNG vào system-prompt; cleanup math_lockdown dead-row (alembic DELETE) + fix doc 04-D | P2-E Q20 + P2-A ↔️ | sacred #5 hold; addend grounded |
| **D-trueBM25** | `ts_rank_cd`→VectorChord-BM25 qua LexicalRetrievalPort, A/B 91Q TRƯỚC ADR | P2-D mục 15 | A/B recall + HALLU=0 |

**Gate W4**: flip↓ + grounding-coverage + HALLU=0.

### W5 — COST + CONFIG GOVERNANCE
| Item | Action | Evidence | Gate |
|---|---|---|---|
| **D9 cost** | EVOLVE `request_steps`→ledger: add `purpose` column (backfill=step_name); cost-by-(tenant,purpose,model) read-query; **ingest cost thật** từ `estimate_batch_cost_usd` (bỏ `0.0`) + parent `ingest_jobs`/NULL-FK | P2-G §3 Option A | per-(tenant,purpose) cost queryable **incl. ingest** |
| **D9 config-lint** | alembic 0020 = bootstrap-of-record, xóa duplicate seed; 4-way value-equality lint CI; fix `validate_constants.sh` trỏ `constants/` package; lift 7 RBAC hardcode level | P2-G DRIFT-1/2 + P2-H RB-1 | 4-way lint green; guard chạy thật |
| **D9 Haiku** | chốt contradiction: Haiku KHÔNG vi phạm (2 governance scope, P2-E #7) → amend CLAUDE.md ban hoặc giữ — decision-doc | register D9 | decision-doc |
| **D10 embed-guard** | chặn đổi embedding model khi có chunks (fail-loud + re-embed flow), không chỉ no-op counter | P2-F embed-change | swap-blocked test |

**Gate W5**: cost queryable + config-lint + embed-guard.

### W6 — APPLICATION EXPERT (đóng vòng học + ops + compliance)
| Item | Action | Evidence | Gate |
|---|---|---|---|
| **D12 feedback-loop** | wire vế đọc: `aggregate_per_bot` reader → analytics; `FeedbackGiven` subscriber; `FAQCandidateService` callsite (refuse→FAQ-candidate); import `admin_refuse_suggestions` vào `router.py` (hiện 404); thumbs→sysprompt-suggestion | P2-I 🐛 D12 | thumbs/refuse → eval/FAQ loop closed end-to-end |
| **D13 ground-truth** | quy trình người-độc-lập gán nhãn (không biết hệ thống); eval judge **đổi cross-family** (khác answer-gen) chống self-bias; agent KHÔNG tự-verify đáp án mình | P2-I D13 | independent-labeler doc + cross-family judge |
| **coverage observable** | analytics tách refuse-đúng-OOS vs silent-miss; coverage live (không chỉ offline GRADED); `RagasMetricAdapter` đo thật (bỏ stub) | P2-I | coverage metric live per-bot |
| **D11 SLO/alert** | wire scheduler gọi `cost_cap_alerter.evaluate_tenants` + p95-breach alert qua `error_notify_hook`+`notify_channel_resolver` (plumbing đã có) | P2-J 🐛-ALERT | SLO-breach alert fires |
| **D11 DR/secrets** | (ops-side) WAL/PITR hoặc pgBackRest (RPO 24h→5min); off-host dump; KEK→KMS; JWT signing-key rotation | P2-J ↔️-DR | restore-drill RPO đo được |
| **D11 PDPL (cập nhật)** | **Nghị định 13 → Luật 91/2025/QH15 + NĐ 356/2025** (hiệu lực 2026-01-01); add consent-lifecycle + data-export (erasure đã có) | P2-J 🕰 | erase+export+consent endpoint |
| **D-webhook** | ingest success-webhook reuse `create_delivery` infra (chat đã có HMAC+retry+rotation) | P2-K ↔️ | ingest-done webhook fires |

**Gate W6**: feedback-loop closed + SLO-alert + PDPL compliance.

### Quyết định CẮT NGANG (Phase 3 ADR, trước wave liên quan)
- **Sacred #10 sysprompt-append ADR** (CRITICAL, `sysprompt_assembler.py:126`): app append 6KB rule platform — phán **governed-exception** (Tier-6 platform-default = config hợp lệ, giữ + làm rõ trong CLAUDE.md) vs **violation** (gỡ, đẩy rule vào bot-template). Quyết TRƯỚC W3/W4 (chỗ đụng prompt). Liên quan sacred posture toàn hệ → ưu tiên ADR sớm.

---

## 3. LUỒNG LÀM (execution flow per item — kỷ luật bắt buộc)

```
GATE 2 (report+plan duyệt)
   │
   ▼
PHASE 3 — RESEARCH + ADR  (per wave, READ-ONLY, agent Fable 5)
   • mỗi decision Dn → ADR: bug-investigation 5-step + SOTA-2026 + 2-3 level (short/mid/long) + trade-off
   • ADR APPROVED mới vào Phase 4   ──► GATE 3 (user approve ADR-set của wave)
   │
   ▼
PHASE 4 — BUILD  (per wave, Opus/Fable theo tier, REVIEWER ≠ BUILDER)
   1. /tdd: viết failing test reproduce TRƯỚC (bug PHẢI fail trước khi sửa)
   2. minimum code (Simplicity-First, surgical, match style)
   3. self-verify: pytest pass + grep zero-hardcode + 4-key + sacred + no-version-ref + naming
   4. ĐO: load-test/RAGAS-parallel / leak-test / EXPLAIN / psql — số trước/sau
   5. Quality Gate 11-item → APPROVED   ──► gate wave (user)
   │
   ▼
PHASE 5 — EVAL + ABLATION  (per wave + tổng cuối)
   • metric trục của wave PHẢI dịch + HALLU=0 hold + 0 regression
   • ablation: semantic-chunk keep/drop, Ekimetrics keep/kill, true-BM25 swap
   • flip-detection 3-5 run   ──► GATE 5 (user approve eval)
   │
   ▼
PHASE 6 — VẬN HÀNH  (SLO live, DR drill, feedback-loop quan sát thật)
```

**Mỗi item trong wave đi đúng vòng**: ADR → failing-test → code → đo → gate. KHÔNG patch-trước-hiểu-sau. KHÔNG fix-sai-tầng (retrieval-bug KHÔNG fix bằng sysprompt). KHÔNG claim-không-evidence.

---

## 4. TRACK SONG SONG (con người — đường găng, charter §critical-path)

Chạy **song song** Phase 3/4 (không chặn code, nhưng chặn Phase 5 eval):
1. **Ground-truth corpus** (D13) — gom 3 corpus thật + người KHÔNG biết hệ thống viết đáp án chuẩn (AdapChunk §9.3). Đây là tiền-đề mọi A/B coverage. **Bắt đầu NGAY.**
2. **Ops provisioning** — `ragbot_app` DSN (W1-D3), WAL/PITR + KMS (W6-D11). Code chờ ops cho 2 mốc này.
3. **Legal review** — PDPL 91/2025 + NĐ 356/2025 scope (W6-D11).

---

## 5. THỨ TỰ & LÝ DO (sequencing rationale)

- **W1 trước hết**: P0 security + data-loss đang chảy máu thật (RLS inert đo được, message-drop đo được). Charter chốt "Wave 1 = RLS+cache-scope = code sửa đầu tiên".
- **W2 trước W3**: workspace-entity + quota là nền cho RBAC/cost-per-ws; chunk-quality (W3) độc lập nhưng cần ground-truth corpus (track người) sẵn sàng.
- **W3 trước W4**: chunk-quality (retrieval input) phải đúng trước khi tune retrieval-determinism (output) — fix đúng tầng, tránh tune trên input rác.
- **W4 trước W5**: answer-determinism ổn rồi mới đo cost chính xác (flip làm nhiễu cost-per-turn).
- **W5 trước W6**: cost/config-governance là nền cho SLO/alert (W6 cần cost-queryable để alert cost-cap).
- **W6 cuối**: application-expert (feedback-loop, ops, compliance) đóng vòng — cần engine xanh (charter: "sau khi engine 6-trục xanh").

> Có thể song song hóa: W3 (chunk) ∥ W1/W2 (security) vì khác tầng. Nhưng eval-gate mỗi wave vẫn tuần tự để attribute đúng metric.

---

## 6. ĐỊNH NGHĨA "XONG" (expert = đo được, không cảm tính)

**Expert RAG** = 6 trục xanh: HALLU=0 + faith≥0.95 (ĐÚNG) · coverage≥0.95 đo-live (ĐỦ) · leak-test CI 0-row as ragbot_app (AN TOÀN) · p95 tier + SLO-alert (NHANH) · cost-per-(tenant,purpose) incl-ingest (RẺ) · feedback-loop closed + mọi quyết định log-lý-do (KIỂM SOÁT).

**Expert Application** = bot-owner self-service đủ (sysprompt edit+preview, không append mù; quota/RBAC workspace thật) + feedback→học loop + analytics coverage/miss + PDPL compliance + SLO/DR ops-grade + API multi-channel/webhook đối-xứng.

---
*EXPERT-PLAN draft. 0 code chạm. program/ UNTRACKED — chờ user duyệt GATE 2 + commit. Next = GATE 2 approve → Phase 3 ADR (bắt đầu W1: D3/D8b/D4 + API-key + get_graph-DI + sacred#10-ADR).*
