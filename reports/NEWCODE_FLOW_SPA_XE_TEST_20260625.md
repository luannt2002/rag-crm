# New-code flow + upload/query test — spa & xe (2026-06-25)

Verifies the new code (T2 column_roles + G4 advisory + measure-unit fix + Track B
language→language_packs) end-to-end on the **raw upload → ingest → query** flow for two
bots, with per-question step diagnosis.

## New code under test (5 commits)
| Commit | Change | Layer |
|---|---|---|
| `5bf1792` | T2 per-bot `custom_vocabulary["column_roles"]` (ADR-0006) + G4 ingest data-quality advisory + `_is_header_row` accepts owner labels | ingest |
| `2ae8945` | measure-unit guard `buoi/buoc` → "bao nhiêu bước/buổi" routes to vector | retrieve route |
| `314ad43`/`97286b9`/`7576301` | domain-neutral guard + customer-literal scrub + Track B language→`language_packs` (wired) | engine-wide |

## Upload flow (NORMALIZE-to-IR: raw Google links, no happy-case rewrite)
| Bot | Links | Result | Stats entities | G4 advisory |
|---|---|---|---|---|
| **spa** (test-spa-id) | 4 sheets | 4/4 active (1/8/1/33 chunks) | 189 | fired (2 events) |
| **xe** (chinh-sach-xe) | 3 sheets + 1 doc | 4/4 active (97/1/213/8 chunks) | 496 | fired |

✅ New code does NOT break ingest; G4 + stats extraction run on real raw data for both.

---

## SPA — 42/50 = 84% (run 22:29; flaky range 82–86%)
| Nhóm | Score |
|---|---|
| Thông tin cơ bản | 3/5 = 60% |
| **CSD CNC & Buffet** | **10/10 = 100%** ← measure-unit fix held |
| Các dịch vụ nổi bật | 5/8 = 62% |
| Dưỡng sinh/Massage | 6/7 = 86% |
| Triệt lông | 8/10 = 80% |
| **Quy trình tư vấn** | **10/10 = 100%** ← measure-unit fix held |

### SPA failures by STEP
| Câu | Bot | chk/sc | STEP | Loại |
|---|---|---|---|---|
| Giờ mở cửa | "9h-21h, thứ 2→CN" ✅ | 1 / 0.43 | **SCORER** (bot đúng) | not-bug |
| Fanpage/Tiktok link | refuse | 1 / 0.06 | **DATA** (0 hit/4 sheet) | data absent, refuse đúng |
| Ultherapy giá gốc/KM | "chưa thấy Ultherapy" | 1 / **1.0** | **ROUTE+INGEST** (stats hijack; giá ở prose, no entity) | Track A |
| Căng bóng trải nghiệm 249k | refuse | 1 / **1.0** | **ROUTE+RENDER** (prose "249K" not in chunk) | Track A |
| Combo Nâng cơ Dr Medi 8tr | refuse | 1 / **1.0** | **ROUTE+INGEST** (combo ở prose) | Track A |
| Tẩy da chết 550k | refuse | 1 / **1.0** | **ROUTE** (variant 450k vs 550k sai) | Track A |
| Triệt lông trải nghiệm 49k | refuse | 1 / **1.0** | **ROUTE+RENDER** (prose "49K") | Track A |
| Hiệu quả 3-5 buổi | "1-2 buổi" | **4 / 0.47** | **DATA** (đã vào VECTOR; source mâu thuẫn spa-2 "1-2" vs gold "3-5") | not-bug (faithful) |
| Triệt râu combo | **2.490.000** (HALLU) | 1 / **1.0** | **RENDER** (entity có combo 1499000 nhưng chunk render chỉ price_primary 249k → LLM ×10) | Track A + HALLU |

**SPA verdict**: 1 scorer + 2 data (refuse/faithful đúng) + **6 fixable = stats ROUTE/RENDER (Track A)**. Bot thật ≈ 46-47/50 ≈ 93%.

---

## XE — 28/40 = 70%
| Nhóm | Score |
|---|---|
| N1 Thông tin sản phẩm | 9/10 = 90% |
| N2 Tồn kho & Giá | 4/8 = 50% |
| N3 Chính sách bảo hành | 10/12 = 83% |
| N4 Hàng đang về | **5/5 = 100%** |
| **N5 Hình ảnh & Date SX** | **0/5 = 0%** |

### XE failures by STEP (12)
| Câu (exp) | chk/sc | src (synthetic) | STEP |
|---|---|---|---|
| N1 Mã hàng NEOTERRA 195/65R16 (`2-R16 195/65 NEO`) | 1 / **1.0** | tên SP, thiếu Mã | **RENDER** (chunk drop "Mã hàng" attr) |
| N2 Tồn `2-R14...` (`404`) | 1 / **1.0** | tên SP, thiếu Tồn | **RENDER** (drop "Tồn kho" attr) |
| N2 Tồn `2-R16 205/55 LPD` (`780`) | 1 / **1.0** | tên SP, thiếu Tồn | **RENDER** |
| N2 Tồn Rovelo 155 (`134`) | 1 / **1.0** | "Rovelo: 774000" (chỉ giá) | **RENDER** |
| N2 Tồn `2-R13 175/70 LPD` (`23`) | 1 / **1.0** | tên SP, thiếu Tồn | **RENDER** |
| N3 Hotline (`0988 771 310`) | 1 / **1.0** | "Địa chỉ: Kho Hải Ngân" | **ROUTE+RENDER** (stats hijack contact query) |
| N3 Địa chỉ kho Nam Phát | **1 / 0.43** | policy chunk (vector) | **RETRIEVE/LLM** (vector sai chunk) |
| N5 Date SX `2-R16 195/65` (`26`) | 1 / **1.0** | tên SP, thiếu date1 | **RENDER** (drop "date1" attr) |
| N5 Link ảnh `2-R14 165/...` (drive link) | 1 / **1.0** | tên SP, thiếu link | **RENDER** (drop image-link attr) |
| N5 Date `2-R14 185...` (`25`) | 1 / **1.0** | tên SP, thiếu date1 | **RENDER** |
| N5 Date `2-ZR17 225/50` (`26`) | 1 / **1.0** | tên SP, thiếu date1 | **RENDER** |
| N5 Link ảnh LANDSPIDER 155/80R13 (drive link) | 1 / **1.0** | tên SP, thiếu link | **RENDER** |

**XE verdict**: **11/12 = stats synthetic chunk DROP generic attribute** (Tồn/Date/Link/Mã) — `chk=1 sc=1.0`. 1 = vector wrong-chunk (address).

---

## 🎯 Root cause chung (cả 2 bot, 1 chỗ)
**`chk=1 sc=1.0` = stats synthetic chunk hijack + render chỉ surface `price_primary`/tên, NUỐT mọi generic labelled attribute** (`attributes_json`).
- spa: combo price, trải nghiệm price → 6 câu
- xe: Tồn kho, Date SX, Link ảnh, Mã hàng → 11 câu

DB entity CÓ đủ data (verified: spa Râu `attributes_json:{"Giá Combo 10 buổi":1499000}`; xe entity có Tồn/Date/Link trong attributes_json từ raw). **Data đúng — chết ở step RENDER (DB→LLM).**

→ **1 fix = Track A S1 (render-faithful generic `attributes_json`)** đóng **~17 câu** (6 spa + 11 xe) + HALLU triệt râu. **Domain-neutral**: render label owner tự đặt ("Giá Combo"/"Tồn kho"/"Date"/"RAM") — không special-case. Đúng ADR-0006 + ADR-0007 S1.

## Không phải bug (rule#0)
- spa scorer FN (giờ mở cửa), spa data-absent (fanpage), spa source-contradiction (3-5 buổi), xe vector wrong-chunk (1 địa chỉ).
- **0 câu fail vì Track B (language) hay scrub** → code mới sạch, backward-compat.

## Tỉ lệ sai tóm tắt
| Bot | Pass | Sai | Sai vì RENDER/ROUTE (Track A) | Sai khác (data/scorer/vector) |
|---|---|---|---|---|
| spa | 84% (42/50) | 8 | 6 | 2 (+1 scorer) |
| xe | 70% (28/40) | 12 | 11 | 1 |
| **gộp** | **78% (70/90)** | **20** | **17 (1 root)** | **3** |
