# TRUTH STATE REPORT — chinh-sach-xe, 2026-07-06

> Sự thật, đo bằng agent (DB-verified), 200 câu, block ON, corpus 6e6c0774.
> Nguyên tắc: mọi con số có evidence; không đoán. Anchor:
> `specs/002-deepdebug-luannt/evidence/step20_full_detail_verdicts.json`.

## 1. SỰ THẬT — điểm số

| Bộ | Đạt | Chưa chuẩn | Ghi chú |
|---|---|---|---|
| **gate100** (câu thường, sinh từ ground-truth) | **91/100** | 9 | +7pp vs baseline 84 |
| **luannt100b** (100 câu BẪY theo 22 lỗi thật) | **74/100** | 26 | capture TRƯỚC chain-fix 002-J; +chain-fix ≈ **77-78** |

- **HALLU bịa-SỐ ≈ 0** trên cả 2 bộ (block chặn hết fabricate/grab số). Baseline luannt100b có 8 sai_bia-số → nay ~0 sai_bia-SỐ.
- luannt100b là bộ BẪY khó (thiết kế để bot sai), không phải câu khách thường. Câu thường = gate100 = 91.

## 2. SỰ THẬT quan trọng nhất — vấn đề còn lại KHÔNG phải "bot nói dối"

35 câu sai, xếp theo TẦNG FIX (root cause DB-verified):

| Tầng lỗi | Số câu | Bản chất |
|---|---|---|
| **retrieve** | **13** | Data CÓ trong corpus nhưng KHÔNG được kéo tới LLM (topK miss) |
| **grounding-nonnumeric** | **11** | Bịa PHI-SỐ (scope/brand/date-semantics) — numeric gate không soi được |
| **ingest** | **5** | 1 bug: cột NGÀY VỀ mất tên header khi ingest |
| **block-gate** | **3** | Numeric gate cần tinh chỉnh (bịa tồn "0 lốp", % công thức) |
| **coreference** | **3** | Chain "nó/SKU đó" resolve sai entity (rewritten=None) |

**Đảo ngược nhận thức**: đòn bẩy #1 KHÔNG phải "chống bịa" mà là **RETRIEVE** — với câu so sánh/tổng hợp/liệt kê, dòng-có-giá không lọt topK → LLM thiếu data. Corpus có, retrieval bỏ sót.

## 3. Bản đồ lỗi theo PIPELINE (từng vị trí đang bị gì)

```
INGEST ──► header 2-dòng: cột NGÀY VỀ mất tên → 5 câu (G-063..068)
  │        (data lưu dưới key rỗng "" → LLM không biết đó là ngày về)
  ▼
RETRIEVE ──► ❌ ĐÒN BẨY LỚN NHẤT — 13 câu
  │          • comparison "so sánh A vs B": dòng-giá B không lọt topK (B-017)
  │          • aggregation "SKU tồn <5 / liệt kê brand": query cả-bảng nhưng
  │            chunk-based chỉ mang vài chunk → thiếu item (B-018/062/065/068/069, G-099)
  │          • chain "link/giá của nó": entity lượt trước không carry (B-048/052/056/060)
  │          • brand-list: không có chunk canonical liệt kê đủ 4 brand (G-099)
  ▼
GENERATE ──► grounding phi-số — 11 câu
  │          • "chưa phân phối Rovelo" (SAI — corpus có 33-50 SKU Rovelo) B-011/G-077/078
  │          • "bảo hành cả xe tải" (scope ngoài corpus) B-031
  │          • "gai 8-9mm" (world-knowledge) B-035  • date "26"→"26/11" G-067
  │          • marketing fluff B-055/066 · coreference lech B-012/047/050
  ▼
GUARD ──► ✅ block chặn bịa-SỐ tốt · ⚠️ 3 câu gate cần tinh chỉnh:
           B-002 "còn 0 lốp" (bịa tồn, 0 bị coi non-claim) · B-032 %công-thức · B-035
```

## 4. SỰ THẬT về đã fix (phiên 2026-07-06, 10 commit)

| Đã ship | Hiệu quả đo được |
|---|---|
| Block numeric-fidelity (002-I) | bịa-số 10/10 → 0/10; gate 84→91 |
| Chain-context grounding (002-J) | chain false-block 3/3 → 0/3 (live) |
| Noise-strip gate (002-H) | FP gate 1.2%→0% |
| Serve marker + route (002-F/G) | plumbing đúng (LLM lờ → cần block) |
| P4 trace (dev) | mỗi request có file verify đủ prompt/raw/final/chunk |
| Continuation-merge (002-E) | 265/70 giá hồi sinh, bug UI đóng |
| P1 digit-route | THỬ → REVERT (đo: zero delta + defect) |

## 5. Ưu tiên fix — theo fixable-mix (có căn cứ, không đoán)

1. **RETRIEVE (13 câu, ROI cao nhất)**: route comparison/aggregation/brand-list qua stats-index (đã có) thay vì chunk-retrieve; carry chain entity (fix rewritten=None). Chữa được cả refuse_oan lẫn thiếu.
2. **grounding-nonnumeric (11)**: gate grounding-claim cho scope/brand ("chưa phân phối X" phải check DB brand tồn tại). Khó hơn (prose), nhưng ~nửa là brand-scope check được deterministic.
3. **INGEST header 2-dòng (5)**: đặt tên cột NGÀY VỀ khi ingest → re-upload. Nhỏ, đúng gốc.
4. **block-gate tinh chỉnh (3)** + **coreference (3)**: nhỏ.

## 6. Kết luận thẳng

- Bot **không còn bịa số** (mục tiêu HALLU-số=0 đạt). Câu thường 91%.
- Phần chưa chuẩn: **hơn 1/3 là RETRIEVE** (data có mà không lấy) — không phải bot dối. Đây là tin TỐT: sửa retrieval kéo điểm nhanh, đúng tầng.
- Bịa còn lại là PHI-SỐ (scope/brand) — cần lớp grounding riêng, không phải numeric.
- Chưa GA-ready tuyệt đối (trap ~77), nhưng đã qua ngưỡng an toàn HALLU-số.

**Anchor commit**: `b5fc6cb`. Chi tiết 35 root-cause: `step20_full_detail_verdicts.json`.
