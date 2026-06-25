# QA chinh-sach-xe — corpus-derived 18Q, agent-scored (no LLM judge) — 2026-06-24

Bot: chinh-sach-xe (lốp Nam Phát, 172 products / Landspider+Rovelo+các loại).
Recipe: /api/ragbot/test/tokens/self (X-Loadtest-Bypass) → POST /api/ragbot/test/chat, bypass_cache=true, debug=full.
Ground truth from xe-3.csv (canonical: Tên,Giá,Mã,Số lượng,Aliases) + xe-4.md (policy). Scoring read from evidence (intent / top_k / score_max / chunks / answer + DB document_service_index).

| id | q | expected (corpus) | answer (bot) | chunks | intent / score_max | verdict |
|----|---|------|------|--------|-------|---------|
| f1 | 265/50R20 giá? | A/T 2.601.000 **+** ZR20 H/P 2.322.000 | only A/T 2.601.000 | 1 | aggregation / 1.0 | 🔴 RETRIEVAL (1 of 2 — Z-variant dropped) |
| f2 | 155/80R13 giá? | G/P 684.000 | 684.000 ✓ | 1 | aggregation / 1.0 | ✅ CHUẨN |
| f3 | 285/45ZR22 giá? | H/P 3.735.000 | 3.735.000 ✓ | 1 | aggregation / 1.0 | ✅ CHUẨN |
| v1 | có bao nhiêu loại 265/50R20 | 2 (A/T + ZR20 H/P) | only A/T (1 loại) | 1 | aggregation / 1.0 | 🔴 RETRIEVAL (missing variant) |
| v2 | 265/50R20 có những loại nào | 2 | only A/T | 1 | aggregation / 1.0 | 🔴 RETRIEVAL (missing variant) |
| v3 | có mấy loại 265/65R17 | 3 | all 3 ✓ (1.854 / 2.322 / 2.430) | 1 | aggregation / 1.0 | ✅ CHUẨN |
| v4 | 245/65R17 có mấy loại | 2 | both ✓ (1.755 H/T + 2.268 A/T) | 1 | aggregation / 1.0 | ✅ CHUẨN |
| v5 | 185/65R15 có những loại nào | 2 | both ✓ (810 Rovelo + 900 LAND) | 1 | aggregation / 1.0 | ✅ CHUẨN |
| a1 | 265 50 20 giá? (space alias) | 265/50R20 → 2 | only A/T | 1 | aggregation / 1.0 | 🔴 RETRIEVAL (alias OK, missing Z-variant) |
| a2 | 2655020 (no-sep alias) | 265/50R20 → 2 | only A/T | 1 | aggregation / 1.0 | 🔴 RETRIEVAL (alias OK, missing Z-variant) |
| a3 | Land 265/50R20 A/T giá? | A/T 2.601.000 | 2.601.000 ✓ | 1 | aggregation / 1.0 | ✅ CHUẨN |
| a4 | giá lốp 155 80 13 | 684.000 (in corpus) | "chưa tìm thấy" (REFUSE) | 1 | **factoid / 0.0** | 🔴 RETRIEVAL (intent mis-route → vector misses alias) |
| ag1 | lốp nào rẻ nhất | Rovelo 155/70R13 630.000 | asks for quy cách (no answer) | 1 | aggregation / 1.0 | 🟡 GENERATION (min op not run; bot punts) |
| ag2 | lốp nào đắt nhất | LAND 285/45ZR22 3.735.000 | asks for quy cách (no answer) | 1 | aggregation / 1.0 | 🟡 GENERATION (max op not run; bot punts) |
| l1 | có những lốp 16 inch nào | ~30 R16 products | only 2 listed (195/60R16, 195/55R16) | 1 | aggregation / 1.0 | 🔴 RETRIEVAL (list truncated 2 of 30) |
| p1 | thời gian bảo hành bao lâu | 05 năm / gai ≥1.6mm | trả "3 tháng đổi mới 100%" (wrong fact) | 1 | factoid / 0.0 | 🟡 GENERATION (answered adjacent policy, not the 05-năm fact) |
| p2 | gai >70% bảo hành thế nào | Đổi mới 100% 01 lốp nếu lỗi NSX | đúng ✓ | 1 | factoid / 0.0 | ✅ CHUẨN |
| t1 | 235/35R20 giá? (NOT in corpus) | refuse | "chưa tìm thấy 235/35R20" ✓ | 1 | aggregation / 1.0 | ✅ CHUẨN (refuse trap honored) |

## Rollup
- Answered-correctly (✅): 8/18 = **44%**
- HALLU (🟠 fabricated): **0/18** — sacred HOLDS (trap t1 refused; no invented prices/sizes).
- Missing-variant / truncated-list failures (🔴 RETRIEVAL): **6** (f1, v1, v2, a1, a2, l1) + a4 (intent route) = 7 retrieval-layer.
- 🟡 GENERATION: 3 (ag1, ag2, p1).
- p95 latency ≈ 9.2s.
