# SỰ THẬT HIỆN TẠI — Ragbot (rag-crm) · 2026-07-09

> Hợp nhất: 52-CONFIRMED all-flows audit + remediation roadmap (reclassify correctness) + 500Q audit
> (B/T/UP/QI/CA đã xong, evidence `file:line` + SQL live). Phần 500Q còn lại (P/CL/CH/EN/EM/ST/W +
> UN/CV/QT/RT/RR/GR/GE/GO/RP/AU/FL/HC/DC/EV/DB/TR) **chạm session-limit, resume 13:20**.
> Rule #0: mọi dòng có `file:line` hoặc SQL thật; không tin README/STATE/docs.

---

## 0. Một câu sự thật

**Khung expert thật, bot trả lời TỐT (correctness thật ~95.9%/95.3%, cao hơn báo cáo), nhưng "đã CÓ ≠ đã BẬT ≠ đã TỐT": rất nhiều cỗ máy expert đang INERT/DEAD/DRIFTED, luồng deploy/RLS/config chưa nối hết dây, và perf nghẽn 100% ở LLM ngoài — KHÔNG phải code BE.**

---

## 1. CHẤT LƯỢNG — bot tốt hơn báo cáo (reclassify DB thật)

| Bot | Báo cáo | **THẬT** | Correction |
|---|---|---|---|
| XE | 94% | **95.9%** (93/97) | G-064, G-095 là infra 5xx gán nhãn logic |
| SPA | 92% | **95.3%** (82/86) | 4 coref là **lỗi HARNESS** (history_msgs=0, không gửi lượt trước), KHÔNG phải bug bot |

**8 câu SAI-logic THẬT còn lại** (đã verify DB): comparison G-097/098 (rớt ở **rerank cut**, không phải decompose) · arrival G-063/067 (bảng "NGÀY VỀ" là chunk RIÊNG không link entity giá) · coverage S-046/075/039 (surfacing variance) · **HALLU S-005** (bịa hotline — Fix#1 hôm nay đã bắt). Nghẽn vận hành lớn nhất KHÔNG nằm ở đây: **innocom 5xx** (7/7 fail rows) = external, cần failover.

## 2. "ĐÃ CÓ ≠ ĐÃ BẬT ≠ ĐÃ TỐT" — cỗ máy chết/trơ/lệch (evidence)

| Thứ | Trạng thái thật | Evidence |
|---|---|---|
| AdapChunk block-pipeline | flag ON nhưng **no-op** | `ingest_stages.py:582-668` cả 2 nhánh về `smart_chunk(flat)` |
| `rrf_round_robin` (fairness so-sánh) | **dead**, 0 src import | chỉ test import |
| `extract_all_codes` | định nghĩa+test, **chưa wire** | `query_range_parser.py` 0 caller |
| understand cache | (đã fix hôm nay) trước đó **không ghi** | `understand.py:282` function-as-bool |
| **RLS 3-lớp** | **100% trơ** (proven live) | probe: `postgres` + `SET app.tenant_id='0000'` → **thấy cả 6 bots** (T11); `DATABASE_URL_APP` unset, `ragbot_system` role MISSING (T16) |
| MMR 0.98 / cliff 0.05 | hằng ≠ DB (0.88/0.2) | drift |
| config parity-guard | mù 152 pcfg ở nodes/ | `test_pipeline_cfg_keys_parity.py:35` |
| `check_config_completeness` gate | **advisory, 0 CI job** | 0 hit trong `.github/workflows/` (B14) |
| length_limit guard (8000) | **UNREACHABLE** (schema cap 2000/4000) = dead | QI04 |
| text_normalizer / BartPho accent | **null / 0 caller** = dead | QI05 |
| `tenant_model_policy` | **0 rows** = dormant | T29 |
| `/documents/check` happy-gate | **KHÔNG TỒN TẠI** | UP18 |
| `/ready` (deploy.sh poll) | **KHÔNG TỒN TẠI** → readiness loop hỏng | B12 |

## 3. BẢO MẬT / cô lập — gap thật

- **RLS INERT** (mục 2): cô lập thật chỉ nhờ `WHERE record_bot_id` ở app (rigorous nhưng 1 lớp). Live chỉ **1 tenant sở hữu cả 6 bot** → cross-tenant leak chưa bao giờ bị test runtime.
- **Prompt-injection guard EN-only** (QI03): pattern khớp "ignore previous instructions" nhưng **"bỏ qua hướng dẫn trước đó" (VN) → BYPASS**. Bot Việt lọt injection.
- `bots.bypass_token_check` = **6/6 TRUE** (T14) — mọi bot bỏ qua token check.
- Soft-delete bot → CASCADE FK **không fire** → chunks/docs/cache/conversations **ORPHAN** (T27).
- NULLIF('') hardening **chưa áp** (T17).

## 4. DEPLOY / PROCESS / WORKER — drift nguy hiểm

- **--workers 2 double-consume**: prod thật systemd `--workers 1`, nhưng `start.sh`/`deploy.sh` ghi **`--workers 2`** → 2 bản embedded-worker (B03). *(Ingest-consumer dùng consumer-group cố định `documents:document-worker` UP10 → chia tải an toàn; nhưng recovery/cost-cap/cache-purge chạy đôi.)* → **Fix: assert `embed_workers ⇒ workers==1` fail-loud**, hoặc tách API/worker (horizontal mode đã có).
- **config-gate ngoài CI** + **fail-loud CHƯA có**: thiếu `system_config` key = `system_config_service.py:92 return default` nuốt lỗi (B15). → luồng đúng: CI spin-DB → alembic → **gate strict (chặn build)** → deploy → `/ready`. Rồi mới flip fail-loud cho REQUIRED key.

## 5. CONFIG DRIFT (4-state)

- **71/175 key batch-load CHƯA seed** → rơi về code constant (gate đã đo, chưa chặn CI).
- **max-chars 4-state drift**: code `500_000` / HTTP-inline `2_000_000` (hardcode) / live DB `2000000` / comment "worker check" (UP03).
- 83 flag (41 ON/42 OFF) — comment "OFF" lệch code "ON" ở parallel flags.

## 6. INGEST — sự thật

- **"1 API upload" VI PHẠM**: 3 luồng ingest live (`/documents/create` + `/sync/documents` + `/test/.../documents`) + 2 rechunk + 1 dead (UP01).
- **VN_segment (U6) = 97% thời gian ingest** (9009ms/9330ms, UP20) — hotspot ingest thật (không phải embed/parse).
- **Không có filter `state=active` ở retrieve** — DRAFT vô hình chỉ nhờ 0-chunk "tình cờ", không phải guard (UP15).
- Worker **CÓ refetch source_url** cho http URL (cố ý, defeat stale-body bug) — ngược claim "no refetch" (UP11).

## 7. PERF — nghẽn 100% ở LLM ngoài, KHÔNG phải BE

- p50 **45.6s** / p95 110s. BE nhanh (retrieve 88ms, rerank 1.5s, grade 0.37s).
- 3 nguồn nghẽn: understand **15s mọi câu** (cache đã fix) + **sync grounding timeout 30s trên 69%** + retry **3×90s**.
- Root bất biến: **innocom endpoint chậm 3-30s/call + 5xx** (external). Đòn bẩy lớn nhất = failover/đổi provider.

## 8. ĐÃ FIX + MERGE MAIN hôm nay (đã push)

`9cdd4c6` numeric-fidelity hết mù số ĐT (S-005 bắt được, đo thật) · `b88dcc9` understand cache sống lại · `5fe952b` worker self-heal SQLAlchemyError · `485ef25` config gate + dead-key + extract util · docs. Tất cả TDD + đo, 0 regression.

## 9. ƯU TIÊN (roadmap, đã adversarial-verify)

**do_now (an toàn):** worker-assert (chống double-consume) · `/ready` route · config-gate vào CI · URL-ingest OOM bound · persist is_correct (mở khoá measure — design cũ là placeholder rác, đã bắt).
**measure_first:** async grounding (gate floor theo block-mode) · comparison entity-quota **ở RERANK** (không phải retrieve — design cũ FLAWED, đã sửa) · retry 3→2.
**defer_external:** innocom failover/đổi provider (nghẽn lớn nhất) · RLS flip DSN (cần `ragbot_app` cred + tạo `ragbot_system` role) · fail-loud REQUIRED (sau khi gate CI).

## 10. Số CHƯA VERIFY (đánh dấu để không tin nhầm)

- Cross-tenant leak runtime (chỉ 1 tenant, chưa exercise) · Recall@10 · hit-rate cache · cost embed/rerank (ledger KHÔNG emit embed/rerank = leak) · phần 500Q còn lại (resume 13:20).

*Nguồn: journals `wf_9c821e24-5d9` (B/T/UP) + `wf_5ed1e2b1-4ed` (QI/CA) + `wf_83fa6a69-1ba` (roadmap) + `ALLFLOWS_DEEP_AUDIT_SYNTHESIS_20260709.md`. Mọi SQL chạy live DB ragbot_v2_dev.*
