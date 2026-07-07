# [T1-Smartness] Expert Solution — Data-Structure Manifest + Incremental Gate Program

**Ngày**: 2026-07-07 · Nhánh: `fix-260623-ingest-expert` · Bot ref: chinh-sach-xe
**Chuẩn**: CLAUDE.md — RED-test-first, one-change-per-step, đo N≥10, domain-neutral,
Port+Registry+DI, no app-inject (sacred #10), zero-hardcode, evidence-only.

## Evidence gốc (đo, DB-verified — step20_full_detail_verdicts.json)
gate100 91/100 · luannt100b 74/100 · HALLU-số ~0. **35 fail**, **27/35 (77%) chung
1 gốc**: upload hiểu cấu trúc THÔ (doc_profile chỉ đếm bảng để chọn chunk, VỨT;
summary_json aggregate; DSI tên-cột hỏng NGÀY VỀ→"") + KHÔNG truyền schema xuống
query/generate. 3 biểu hiện: ingest cột mất tên (5) · retrieve không schema-aware
routing (11) · generate LLM không biết ranh giới data → bịa brand/mô-tả (11).

## Chiến lược: 2 track SONG SONG (EVOLVE, không rewrite)
- **Track A — MANIFEST (gốc rễ, đòn bẩy 77%)**: xây `DataStructureManifest` per-doc
  ở ingest + nối dây xuống query (routing) + generate (serve labeled + gate biết
  ranh giới). Port+DI. Đây là best-practice TableRAG schema-card.
- **Track B — INCREMENTAL gate (nhanh, đo được, feed vào A)**: 2 gate deterministic
  đã-verify (brand-scope, description-absence) — mỗi cái là 1 "manifest-fact"
  dùng ngay, sau này đọc từ manifest. Không phí công: gate logic tái dùng trong A.

## TRACK A — MANIFEST (phases)

### A0 — Design ADR (hard-to-reverse → cần ADR)
`docs/adr/` — schema của manifest, nơi lưu (cột `documents.structure_manifest_json`
alembic), Port contract, config-gate per-bot. Owner approve trước khi build.

### A1 — Build manifest ở ingest (Port+Registry+DI)
- `application/ports/manifest_port.py` — `ManifestBuilderPort` (Protocol).
- `infrastructure/manifest/table_manifest_builder.py` — từ parsed blocks + DSI:
  `tables[]{name, format, n_rows, columns[]{name, role, dtype, coverage_pct,
  null_count}}, brands[], entity_count, price_range`. Domain-neutral (đọc header
  + role inference sẵn có, KHÔNG vocab brand cứng).
- `infrastructure/manifest/null_manifest.py` — default OFF, không raise.
- `bootstrap.py` DI + config key `manifest_enabled` (system_config).
- Xử lHEADER XẤU (xe-2 Chinese+col3+label-in-row): detector "label-in-data-row"
  → gán tên cột thật (NGÀY VỀ). ĐÂY cũng đóng luôn 5 fail ingest.
- RED test: xe-2 raw → manifest có column "NGÀY VỀ" coverage; xe-3 có price col
  173/187. Đo: re-ingest → DSI attributes có key "NGÀY VỀ" (không còn "").

### A2 — Serve manifest xuống QUERY (schema-aware routing)
- retrieve.py đọc manifest: intent=comparison/aggregation + doc là priced-table
  → route stats (không decompose-skip). aggregation-quantity → quantity-route mới.
  brand-list → serve manifest.brands[].
- RED test + đo N=10 các câu B-017/018/G-099.

### A3 — Serve manifest xuống GENERATE (biết ranh giới data)
- generate serve schema-card ("bảng này có cột: name/price/qty/date; KHÔNG có
  cột mô-tả") như DATA (sacred #10 safe — mô tả cấu trúc, không phải instruction).
- Đo: bịa mô-tả B-055/063/066 giảm.

## TRACK B — INCREMENTAL GATE (làm TRƯỚC, feed A)

### B1 — Brand-scope gate (ĐÃ verify chuẩn) ✅ LÀM NGAY
- guard: answer phủ nhận "chưa phân phối <brand>" + DSI có entity brand đó →
  block bằng owner oos_template (như numeric-fidelity block, sacred-#10 path).
- Deterministic: extract brand-token từ answer-negation + query DSI brand exists.
- RED test → đo B-011/G-077/G-078 (+ non-regression brand thật không có).

### B2 — Description-absence gate
- guard: nếu manifest/DSI cho doc KHÔNG có trường mô-tả (chỉ name/price/qty/image)
  mà answer chứa claim mô-tả tread ("gai/địa hình/High Performance") → flag/block.
- Đo B-055/063/066.

## Thứ tự thực thi
B1 (verify chuẩn, nhanh) → B2 → A0 ADR (owner gate) → A1 (manifest+ingest fix) →
A2 (routing) → A3 (generate). Mỗi step: RED test → 1 change → đo N≥10 → commit.

## KHÔNG trong scope (honest)
- Embedding cross-phrasing (5 câu): track retrieval riêng.
- Corpus gap thật (Vios fitment, gai 8-9mm): chỉ defer.

## Sacred/CLAUDE.md check
- Port+Registry+DI cho manifest builder ✅ · domain-neutral (role inference, no
  brand vocab) ✅ · sacred #10 (gate dùng owner template, manifest = DATA) ✅ ·
  zero-hardcode (config keys) ✅ · multi-bot (record_bot_id scoped) ✅ ·
  RED-test + đo N≥10 mỗi step ✅ · ADR cho A (hard-to-reverse) ✅.
