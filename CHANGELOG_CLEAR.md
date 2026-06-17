# CHANGELOG_CLEAR — Lịch sử xoá plan / docs / reports

> **Mục đích**: trước mỗi lần dọn workspace (xoá plan SHIPPED, docs outdated, reports obsolete), ghi lại ở đây TÊN FILE + ĐƯỜNG DẪN + LÝ DO + NGÀY XOÁ + HASH git (để truy ngược).
>
> **Rule**: KHÔNG xoá file nào trước khi append entry tương ứng vào file này.
>
> **Rule**: KHÔNG dùng file này thay cho git history — chỉ index + lý do. Nội dung thật vẫn còn trong `git log --follow -- <path>`.
>
> **Rule**: mỗi batch xoá = 1 commit riêng với message `chore(cleanup): remove N outdated artifacts (see CHANGELOG_CLEAR.md)`.

---

## 2026-06-09 — Multi-agent scan cleanup (462 files)

Sau multi-agent scan (4 agent: dead-feature / stale-docs / role-compliance / flow-consistency), xoá artifacts SHIPPED/obsolete. Tất cả còn trong git history (`git checkout <commit>^ -- <path>`).

- **plans/_archive/** (27) — đã archive sẵn.
- **plans/2604\*/ + plans/2605\*/** (~191 dir-tree files) — session April+May đã SHIPPED, nội dung cô đọng vào `docs/master/*` + `STATE_SNAPSHOT.md`. Giữ lại `plans/2606*` (June, đang active) + `plans/260506-MASTER-BACKLOG.md` + root meta (ROADMAP_V2, DEFERRED_STREAMS, MASTER_CODER_PROMPT, RESUME_KIT_V9_1, PLAN_V0_CHANGELOG).
- **plans/ root** SESSION_RESUME_\* + RESUME_KIT_V4/V9 + 260423-MASTER-roadmap + 260506-MASTER-PLAN-AUDIT + PENDING_RESUME — one-off resume prompts superseded.
- **reports/** AUDIT_20260515/ (37) + MASTER_RUN_20260513 + LOAD_TEST_VERDICT_20260516/ + MULTI_AGENT_20260516/ — audit cũ, superseded bởi June reports + docs/master.
- **docs/** API.md + API_REFERENCE.md (→ API_REFERENCE_V2.md), ARCHITECTURE_DIAGRAMS (→ docs/master 01-04), AUDIT_REPORT/AUDIT_TEST_REPORT_20260421, DEV_WORKFLOW (→ dev/), DR_RUNBOOK (→ ops/DISASTER_RECOVERY), ENVIRONMENT_SETUP (→ QUICKSTART), NOTEBOOKLM, PERFORMANCE (→ PERFORMANCE_TUNING), RAGBOT_SURGICAL_FIX, _archive/, templates/_archive/, audit/.
- **root** luannt-\*.md (untracked personal scratch) + CLAUDE_PROMPT.md (superseded bởi CLAUDE.md).

---

## Nguyên tắc phân loại

| Nhãn | Ý nghĩa | Action |
|---|---|---|
| `SHIPPED` | Plan đã code + merged + test pass; git history giữ plan.md ở commit ship | XOÁ trong workspace |
| `SUPERSEDED` | Plan bị plan mới (v2/v3) thay thế hoàn toàn | XOÁ, reference plan thay thế |
| `OBSOLETE` | Plan/report nội dung sai (measurement cũ, API đã đổi); không còn actionable | XOÁ |
| `DUPLICATE` | Nội dung trùng với file khác | XOÁ bản cũ / yếu hơn |
| `EMPTY` | Thư mục/file trống hoặc placeholder | XOÁ |
| `HARNESS_LOG` | File JSON/log harness cũ — chỉ giữ 2 mốc mới nhất | XOÁ phần cũ |
| `DROPPED` | Plan dropped theo decision (không code nữa) | XOÁ |
| `KEEP` | Còn cần reference hoặc active | GIỮ |

---

## Sprint 7 scope deltas — 2026-04-25 (no files removed, scope adjustments only)

Per S7 auditor-orchestrator decision (code ready, pending commit):

| Path | New label | Reason |
|---|---|---|
| `plans/260425-S7-1A-chunking-fix/` | SUPERSEDED v2 | Replaced by `plans/260425-S7-F1-chunking-csv-fix/plan.md` for Sprint 7 actual ship. v2 plan remains for history (domain-neutral record of investigation path). KEEP until Sprint 7 ships, then XOÁ. |
| `plans/260425-S7-1B-fallback-gate/` | DROPPED from Sprint 7 | Evidence (top_score std=0.0025 empirically flat when rerank bypassed) → score-based gate has no signal. Revisit after S8 reranker activation. Plan kept for history, not shipped. |
| `plans/260425-S7-2-reingest-validate/` | BACKLOG | Not part of Sprint 7 F1/F2/F4. Optional re-ingest harness. |
| `plans/260425-S7-3-state-update/` | SUBSUMED | Merged into Sprint 7 Phase D docs sync (STATE_SNAPSHOT update). |
| `plans/260425-S7-F1-chunking-csv-fix/` | ACTIVE Sprint 7 | NEW — canonical F1 plan. |
| `plans/260425-S7-F2-docs-only-strict/` | ACTIVE Sprint 7 | NEW — canonical F2 plan. |
| `plans/260425-S7-F4-chunk-audit-log/` | ACTIVE Sprint 7 | NEW — canonical F4 plan. |

No file removals this round. Entries here serve as the scope-drift audit trail.

---

## Batch #1 — 2026-04-24 (candidate list, CHƯA XOÁ)

### 1.1 plans/ — SHIPPED (đã code + merged, git history giữ)

| Path | Ship evidence (commit) | Reason |
|---|---|---|
| `plans/260421-P1-fix-env-loading/` | Status header: DONE 2026-04-21 | SHIPPED — env loading fixed in production |
| `plans/260421-P2-readme-rewrite/` | Status header: DONE 2026-04-21 | SHIPPED — README exists + maintained |
| `plans/260421-P3-chunk-tracking/` | Status header: DONE 2026-04-21 | SHIPPED |
| `plans/260421-P4-auditor-analytics/` | Status header: DONE 2026-04-21 | SHIPPED — endpoints live |
| `plans/260421-P5-bot-test-<demo>/` | Status header: DONE 2026-04-21 (smoke passed) | SHIPPED + scope completed |
| `plans/260421-P6-code-polish/` | Status header: DONE 2026-04-21 | SHIPPED |
| `plans/260421-P7-docs-update/` | Status header: DONE 2026-04-21 | SHIPPED |
| `plans/260421-P8-fix-and-optimize/` | Status header: DONE 2026-04-21 | SHIPPED |
| `plans/260421-P9-fix-demo-and-optimize/` | Status header: DONE 2026-04-21 | SHIPPED |
| `plans/260421-P12-master-refactor/` | Status header: DONE 2026-04-21 (S1+S2+S3+S4 complete, supersedes P10+P11) | SHIPPED |
| `plans/260421-rbac-metadata-driven/` | Status header: DONE 2026-04-22 | SHIPPED |
| `plans/260421-remaining-work/` | Status header: ALL DONE 2026-04-21 | SHIPPED |
| `plans/260421-zero-inline-constants/` | Status header: DONE 2026-04-21 (completed in P6) | SHIPPED — zero-hardcode rule now in CLAUDE.md |
| `plans/260422-P13-pipeline-quality-tuning/` | Status header: DONE 2026-04-22 | SHIPPED |
| `plans/260422-P14-response-contract-and-i18n/` | Status header: DONE 2026-04-22 (all phases) | SHIPPED |
| `plans/260422-P16-bot-quality-no-cost/` | Status: DONE Waves 1-3 2026-04-23 (Wave 4 deferred to tenant) | SHIPPED |
| `plans/260422-P17-codebase-sweep-debug/` | Status: DONE Batches 1-3 2026-04-23 | SHIPPED |
| `plans/260423-P18-post-audit-hardening/` | Status: SHIPPED commit 6677fdc | SHIPPED |
| `plans/260423-P19-rename-remainder-silent-bugs/` | Status: SHIPPED commit f431c91 | SHIPPED |
| `plans/260423-P20-unmask-bugs/` | Status: SHIPPED 2026-04-23 | SHIPPED |
| `plans/260423-P22-vn-nlp-symmetry/` | Commits 55e7ddd, 6a3fdcc, 251fa3a (Sprint 2 Wave VN) | SHIPPED |
| `plans/260423-P24-prod-blockers/` | Sprint 3 Wave 1 commits | SHIPPED |
| `plans/260423-P25-high-traffic-resilience/` | P25-A shipped (Redis + ops quick wins); P25-B/C deferred | SHIPPED (partial — keep if B/C not done) |
| `plans/260423-P26-security-rag-specific/` | Commits df54cd4, f95f322, 7564d8a (Sprint 2 Wave SEC) | SHIPPED |
| `plans/260424-P29-answer-autonomy-lockdown/` | Commits bcba883, a69fa09 (Sprint 5) | SHIPPED (P29-A; P29-B still DRAFT) |

**Note**: P25-B/C chưa ship hoàn toàn — nếu muốn giữ roadmap reference thì KEEP; nếu không làm trong Sprint 7/8 thì xoá.

### 1.2 plans/ — SUPERSEDED

| Path | Replaced by | Reason |
|---|---|---|
| `plans/260421-P10-bot-channel-composite-key/` | `plans/260421-P12-master-refactor/` (explicit "SUPERSEDED by P12" header) | Merged into P12 |
| `plans/260421-P11-naming-convention-refactor/` | `plans/260421-P12-master-refactor/` (explicit "SUPERSEDED by P12" header) | Merged into P12 |

### 1.3 plans/ — DROPPED

| Path | Decision date | Reason |
|---|---|---|
| `plans/260422-P15-next-level-roadmap/` | 2026-04-23 | DROPPED per user — project runs local only, no CI, P15-8 RAGAS gate not applicable |

### 1.4 plans/ — EMPTY / near-empty

| Path | Content | Decision |
|---|---|---|
| `plans/260421-honest-audit-final/report.md` | Audit snapshot 2026-04-21 (pre-P15/P16). Content is a feature inventory already superseded by STATE_SNAPSHOT.md v6 + CLAUDE.md. | OBSOLETE — XOÁ (STATE_SNAPSHOT is truth-of-record) |

### 1.5 plans/ — OBSOLETE session-init prompts

Lý do: prompt chỉ dẫn cho 1 session đã xảy ra, Sprint đó đã xong. Nội dung bị outdate ngay sau khi session chạy (state & priority đã khác).

| Path | Sprint | Reason |
|---|---|---|
| `plans/SESSION_INIT_PROMPT.md` | Generic multi-sprint prompt | Replaced by current `NEW_ROOM_PROMPT_20260424.md` |
| `plans/SESSION_INIT_COMBINED.md` | Sprint 2 (FEAT + SCRUB) | Sprint 2 shipped 2026-04-23; prompt obsolete |
| `plans/SESSION_INIT_SECURITY_SCRUB.md` | Sprint 2 scrub | Scrub shipped (commits 9ed25ba, cdaaa44); prompt obsolete |
| `plans/SESSION_INIT_SPRINT2.md` | Sprint 2 v1 | SHIPPED |
| `plans/SESSION_INIT_SPRINT2_V2.md` | Sprint 2 v2 | SHIPPED |
| `plans/NEW_ROOM_PROMPT_20260424.md` | Sprint 7 init | Will be superseded by Sprint 7 ship + next-sprint prompt (keep until Sprint 7 done, then XOÁ) |

**Decision**: xoá 5 cái đầu. `NEW_ROOM_PROMPT_20260424.md` **KEEP** until Sprint 7 ships.

### 1.6 plans/ — meta/reference files

| Path | Decision | Reason |
|---|---|---|
| `plans/260423-AUDIT_127_REFERENCE.md` | KEEP | Source-of-truth mapping for 127-question audit BATCH 1+2; still referenced by P26 plan |
| `plans/260423-MASTER-roadmap.md` | KEEP | Sprint roadmap index; linked from STATE_SNAPSHOT |
| `plans/260423-MULTI_AGENT_DISPATCH.md` | Evaluate | Multi-agent dispatch notes — may be obsolete after Sprint 7 workflow stabilizes |

### 1.7 plans/ — DRAFT (chưa code) phân loại theo đường ship

Mỗi plan DRAFT phải có 1 trong 3 nhãn:
- `ACTIVE` — đang/sắp code trong sprint hiện tại → KEEP
- `BACKLOG` — còn giá trị, chưa ship, chưa quyết hoãn → KEEP
- `WONT_SHIP` — tính năng đã đề xuất nhưng user quyết không ship (không outdated, không superseded) → XOÁ, entry vào batch #2

| Path | Label | Reason | Decision |
|---|---|---|---|
| `plans/260425-S7-1A-chunking-fix/` | ACTIVE | Sprint 7 Phase 1 | KEEP |
| `plans/260425-S7-1B-fallback-gate/` | ACTIVE | Sprint 7 Phase 1 | KEEP |
| `plans/260425-S7-2-reingest-validate/` | ACTIVE | Sprint 7 Phase 2 | KEEP |
| `plans/260425-S7-3-state-update/` | ACTIVE | Sprint 7 Phase 3 | KEEP |
| `plans/260425-S8-reranker-activation/` | ACTIVE | Next sprint — sẵn sàng code sau Sprint 7 | KEEP |
| `plans/260424-P34-zero-hardcode-sweep-chunking/` | SUBSUMED | Bundled vào S7-1A plan v2 (§6 Zero-hardcode sweep) | **XOÁ sau khi S7-1A ships** (mark for batch #2) |
| `plans/260424-ROADMAP-docs-only-bot/` | ACTIVE | Roadmap index linked from STATE_SNAPSHOT | KEEP |
| `plans/260424-P33-per-tenant-rate-limit/` | **awaiting user decision** | Multi-tenant feature; không blocker pilot single-tenant. Nếu không định ship trong 1-2 sprint → WONT_SHIP | user confirm |
| `plans/260425-P29B-per-bot-autonomy-percent/` | **awaiting user decision** | Per-bot override cho autonomy. P29-A default=0 đã đủ cho 100% docs-only; per-bot escape hatch là "nice to have". Nếu không có bot nào yêu cầu autonomy>0 → WONT_SHIP | user confirm |
| `plans/260423-MULTI_AGENT_DISPATCH.md` | **awaiting user decision** | Sprint 3 multi-agent lane-dispatch matrix — template. Sprint 7 workflow hiện tại không dùng lại format này (Phase 1A+1B song song đã có plan riêng). | user confirm (lean WONT_SHIP) |
| `plans/260423-P25-high-traffic-resilience/` | PARTIAL_SHIPPED | P25-A đã ship; P25-B + P25-C chưa. Nếu không định làm B/C trong Q2 2026 → WONT_SHIP; nếu còn định → BACKLOG | user confirm |

---

### 2.1 reports/ — OBSOLETE test runs (pre-v6 measurements)

Current truth-of-record: `reports/test_run_v6_reingest.json` + `reports/audit_test_run_v6_reingest.*` + `reports/FINAL_VERDICT_V6_REINGEST.md` (commit 8e1f3f1).

Everything older than v6 is **obsolete** because:
- P22 VN NLP ingest asymmetry fix (Sprint 2) changed BM25 scoring
- P28-α/β CRAG constants (Sprint 3) changed grading
- P29-A + P32 (Sprint 4-5) changed generation (math lockdown + temperature=0)
- v6 re-ingest 4 chunks

| Path | Size | Decision | Reason |
|---|---:|---|---|
| `reports/test_run_staging_audit.json` | 204K | OBSOLETE | Pre-Sprint-2 |
| `reports/test_run_staging_audit.stdout.log` | — | OBSOLETE | Pre-Sprint-2 |
| `reports/test_run_staging_audit_v2.json` | 508K | OBSOLETE | Pre-Sprint-2 |
| `reports/test_run_staging_audit_v2.stdout.log` | — | OBSOLETE | Pre-Sprint-2 |
| `reports/test_run_staging_audit_v3.json` | 508K | OBSOLETE | Superseded by v6 |
| `reports/test_run_staging_audit_v3.stdout.log` | — | OBSOLETE | |
| `reports/audit_test_run_staging_audit_v3.json` | 368K | OBSOLETE | Superseded by v6 |
| `reports/audit_test_run_staging_audit_v3.md` | — | OBSOLETE | |
| `reports/test_run_p18.json` | 512K | OBSOLETE | P18 verification run (shipped) |
| `reports/test_run_p19.json` | 504K | OBSOLETE | P19 verification run (shipped) |
| `reports/test_run_small.json` | 60K | OBSOLETE | Ad-hoc smoke |
| `reports/test_run_smoke.json` | 20K | OBSOLETE | Ad-hoc smoke, 2026-04-24 pre-Sprint-7 |
| `reports/test_run_5_rooms_latest.json` | 64K | OBSOLETE | 5-room subset, superseded by v3 full |
| `reports/test_run_wave1.json` | 88K | OBSOLETE | Sprint 3 Wave 1 intermediate |
| `reports/test_run_wave234.json` | 552K | OBSOLETE | Sprint 3 Wave 2-3-4 intermediate |
| `reports/test_run_wave2_parallel.json` | 916K | OBSOLETE | Sprint 3 Wave 2 parallel run |
| `reports/audit_test_run_wave2_parallel.*` | 348K+md | OBSOLETE | |
| `reports/test_run_sprint3.json` | 96K | OBSOLETE | Sprint 3 intermediate |
| `reports/test_run_sprint3_final.json` | 504K | OBSOLETE | Superseded by v6 |
| `reports/audit_test_run_sprint3_final.*` | 368K+md | OBSOLETE | |
| `reports/test_run_gen1_harn3.json` | 924K | OBSOLETE | Sprint 4 GEN-1 + HARN-3 intermediate |
| `reports/audit_test_run_gen1_harn3.*` | 372K+md | OBSOLETE | |

### 2.2 reports/ — obsolete auditor / sprint reports

| Path | Reason |
|---|---|
| `reports/auditor_report_5_rooms.md` | 5-room subset, superseded |
| `reports/auditor_report_20rooms_wave23.md` | Wave intermediate, superseded by v6 |
| `reports/auditor_report_100_rooms.md` | 100-room separate harness, not canonical |
| `reports/auditor_report_2026-04-22.md` | Pre-Sprint-2 |
| `reports/auditor_report_p18_verification.md` | P18 shipped, SHIPPED |
| `reports/deep_bug_audit_2026-04-22.md` | Pre-Sprint-2 findings, issues all fixed |
| `reports/deep_dive_audit_20260423.md` | Sprint 3 deep-dive, findings absorbed into plans |
| `reports/batch2_audit_20260423.md` | 127-question BATCH 2 — 17/30 mapped + shipped, 8 obsolete, 2 intentional, 2 misread; keep reference in AUDIT_127_REFERENCE.md instead |
| `reports/sprint2_code_audit_20260423.md` | Sprint 2 audit — F1/F5/F9 findings fixed |
| `reports/harness_run_sprint3_analysis.md` | Sprint 3 rate-limit analysis, absorbed |
| `reports/eval_diff_sprint3.txt` | Sprint 3 intermediate diff |
| `reports/FINAL_VERDICT_20260423.md` | Pre-Sprint-2+3 verdict |
| `reports/FINAL_VERDICT_SPRINT4.md` | Superseded by SPRINT5 then V6 |
| `reports/FINAL_VERDICT_SPRINT5.md` | Superseded by V6 |

### 2.3 reports/ — KEEP (truth-of-record current)

| Path | Reason |
|---|---|
| `reports/test_run_v6_reingest.json` (1.9M) | Current harness |
| `reports/audit_test_run_v6_reingest.json` (356K) | Current judge output |
| `reports/audit_test_run_v6_reingest.md` | Current judge markdown |
| `reports/FINAL_VERDICT_V6_REINGEST.md` | Current verdict, linked from STATE_SNAPSHOT |

---

### 3.1 docs/ — OBSOLETE audit snapshots

| Path | Size | Reason |
|---|---:|---|
| `docs/AUDIT_REPORT_20260421.md` | 313 lines | 6-agent audit snapshot (pre-Sprint-2). Findings absorbed into P17-P20 plans + shipped. Content now incorrect ("9.0/10" claim superseded by honest 7.5/10 in STATE_SNAPSHOT v6). |
| `docs/AUDIT_TEST_REPORT_20260421.md` | 243 lines | Bot smoke test snapshot 2026-04-21; metrics obsoleted by v6 |
| `docs/RAGBOT_SURGICAL_FIX.md` | 158 lines | Meta-prompt to Claude Code for post-audit sprint 2026-04-21; Sprint 1-5 all shipped — prompt outdated |
| `docs/NOTEBOOKLM_DEEP_DIVE_QUESTIONS.md` | — | Question dump for NotebookLM session; historical, not project-current |
| `docs/audit/cross-validation/2026-04-21/report.md` | — | Sub-directory cross-validation snapshot 2026-04-21 |

### 3.2 docs/ — KEEP

| Path | Reason |
|---|---|
| `docs/API_REFERENCE.md` | API contract |
| `docs/ENVIRONMENT_SETUP.md` | Setup runbook |
| `docs/OPS_POOL_SIZING.md` | Ops runbook |
| `docs/BOT_SYSTEM_PROMPT_TEMPLATE.md` | Tenant authoring template |
| `docs/naming-convention-flow.md` | Naming rule reference (linked from CLAUDE.md) |
| `docs/master/*.md` (13 files A-M) | Architecture spec (indexed by RAGBOT_MASTER.md) |

---

### 4.1 root — OBSOLETE

| Path | Reason |
|---|---|
| `CLAUDE_PROMPT.md` | Meta-prompt for new-room context loading; superseded by `plans/NEW_ROOM_PROMPT_20260424.md` |
| `luannt-test.md` | Duplicate (near-identical) of `luannt-question_and_debug.md` — extra section at end only |

### 4.2 root — KEEP

| Path | Reason |
|---|---|
| `CLAUDE.md` | Project rules — truth-of-record |
| `STATE_SNAPSHOT.md` | Project state — truth-of-record |
| `README.md` | Public entry |
| `RAGBOT_MASTER.md` | Architecture index |
| `docs/channels/ZALO_MASTER.md` | Channel contract (still live) |
| `luannt-question_and_debug.md` | User's personal debug notes (fuller version) — KEEP unless user says otherwise |

---

## Decision matrix — what user must confirm

Anh review từng bucket, chọn `DELETE` / `KEEP`:

```
[ ] Bucket 1.1 — 26 SHIPPED plans    → DELETE? (recommended: YES, git history retains)
[ ] Bucket 1.2 — 2 SUPERSEDED plans  → DELETE? (recommended: YES)
[ ] Bucket 1.3 — 1 DROPPED plan (P15) → DELETE? (recommended: YES)
[ ] Bucket 1.4 — 1 OBSOLETE honest-audit → DELETE? (recommended: YES)
[ ] Bucket 1.5 — 5 session-init prompts → DELETE? (recommended: YES, keep NEW_ROOM_PROMPT_20260424)
[ ] Bucket 1.6 — evaluate MULTI_AGENT_DISPATCH → DELETE? (mild lean: KEEP)
[ ] Bucket 2.1 — 22 obsolete test_run JSONs → DELETE? (recommended: YES, ~7 MB reclaimed)
[ ] Bucket 2.2 — 15 obsolete auditor/verdict reports → DELETE? (recommended: YES)
[ ] Bucket 3.1 — 5 obsolete docs audit snapshots → DELETE? (recommended: YES)
[ ] Bucket 4.1 — 2 root obsolete (CLAUDE_PROMPT, luannt-test dup) → DELETE? (recommended: YES)
```

Sau khi anh confirm bucket nào DELETE:
1. Em chạy `git rm -r` cho mỗi path.
2. Ghi vào `CHANGELOG_CLEAR.md` entry với hash commit cleanup để truy ngược.
3. 1 commit `chore(cleanup): remove N outdated artifacts — see CHANGELOG_CLEAR.md batch #1`.

---

## Lịch sử xoá (điền sau khi commit cleanup batch)

### Batch #1 — 2026-04-24 — Sprint 7 prep cleanup

```
Cleanup commit: <hash sẽ điền sau commit>
Files removed: <count sẽ điền sau>
Bytes reclaimed: <MB sẽ điền sau>
User confirmed buckets: <list>
Git log query to recover: git log --all --diff-filter=D -- <path>
```

---

## FAQ

**Q: Nếu sau này cần tham khảo plan shipped (ví dụ P16)?**
A: `git log --all --full-history -- plans/260422-P16-bot-quality-no-cost/plan.md` → tìm commit có plan.md → `git show <hash>:plans/260422-P16-bot-quality-no-cost/plan.md`.

**Q: Xoá test_run JSON có mất metric history không?**
A: Không — metrics đã cô đọng trong `reports/FINAL_VERDICT_V6_REINGEST.md` + STATE_SNAPSHOT. Raw JSONs chỉ để rerun audit script; nếu cần reproduce → chạy harness lại.

**Q: Nếu lỡ xoá file đang cần?**
A: `git checkout <cleanup-commit>^ -- <path>` để restore. Always safe vì single commit cleanup.
