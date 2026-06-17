# Hướng dẫn Tenant — Sysprompt & Corpus của bot

> **Tóm tắt**: Tenant **CHỈ cần làm theo template mẫu** (như `SYSPROMPT_DR_MEDISPA.md`).
> KHÔNG khuyến khích tenant tự viết sysprompt từ đầu. Tenant tập trung vào **corpus thật**.

---

## Phần 1 — Có khuyến khích tenant tự viết sysprompt không?

### TL;DR

**KHÔNG khuyến khích** tenant tự viết sysprompt từ đầu.

### Lý do

| # | Vấn đề khi tenant tự viết | Hậu quả |
|---|---|---|
| 1 | Bỏ sót Anti-HALLU rules cho con số | Bot bịa giá / số buổi / % → khách lừa nhau |
| 2 | Quên Anti-Fake-Premise / Anti-Fake-Incident | Bot xác nhận tin đồn / scandal giả → reputation risk |
| 3 | Không biết placeholder LLM dễ bịa (`{anh/chị}`, `{tên dịch vụ}`) | Output ra "Dạ {anh/chị} cần em..." raw text |
| 4 | Không biết cấu trúc XML tags (`<role>`, `<rules>`) tăng adherence | LLM ignore rule trong prose dày |
| 5 | Không biết few-shot WRONG vs RIGHT giúp model anchor | Vi phạm rule lặp lại |
| 6 | Không cover decision tree 4 nhánh (greeting / chitchat / vague / specific) | Bot kê 10 dịch vụ khi khách chỉ "hi" |
| 7 | Không biết compliance y tế VN (Luật KCB 2023) | Cam kết "khỏi 100%" → vi phạm pháp lý |

→ Sysprompt tốt = ~12-22K chars + 12 sections + research-based. **Quá khó cho non-engineer**.

### Đúng cách: tenant **COPY template** + chỉnh **chỉ Section 10 (brand context)**

Template `SYSPROMPT_SPA_MASTER_v10.md` có **Section 10 BRAND_CONTEXT** — chỗ duy nhất tenant cần điền:
- Tên thương hiệu
- Slogan (nếu có)
- Hotline / địa chỉ
- 4 nhóm dịch vụ chính (theo tenant)
- Giá trị cốt lõi (theo tenant)
- Ghi chú quan trọng (chi nhánh / dịch vụ NGOÀI catalog)

**11 sections còn lại**: KHÔNG đụng. Đó là rule chung apply cho mọi spa.

---

## Phần 2 — Tenant phải làm gì?

### Tenant TỰ LÀM 3 việc

| Việc | Effort | Risk nếu sai |
|---|---|---|
| **A. Upload corpus thật** | Lớn — phụ thuộc data spa | Bot không biết → refuse nhiều |
| **B. Chỉnh Section 10 sysprompt template** | 5 phút | Bot xưng tên sai |
| **C. Cập nhật `oos_answer_template`** (câu refuse mặc định) | 1 phút | Bot dùng câu refuse generic |

### Tenant KHÔNG TỰ LÀM

| Việc | Lý do |
|---|---|
| Viết sysprompt từ đầu | Quá khó, không cover Anti-HALLU + compliance |
| Chỉnh Anti-HALLU rules | Đã chuẩn, đụng vào dễ break |
| Chỉnh decision tree | Đã research-based |
| Chỉnh refuse template chi tiết | Đã có pattern chuẩn |

---

## Phần 3 — Workflow tenant onboarding

### Step 1: Copy template
```bash
cp docs/templates/SYSPROMPT_SPA_MASTER_v10.md → bots.system_prompt của bot mới
```

### Step 2: Chỉnh Section 10 (BRAND CONTEXT)
Tìm trong file:
```
═══════════════════════════════════════════════════════════════════
SECTION 10 — BRAND CONTEXT (DR. MEDISPA)
═══════════════════════════════════════════════════════════════════
```

Thay nội dung phù hợp với spa của tenant:
```
<brand_context>
Tên thương hiệu: <TÊN SPA CỦA TENANT>
Slogan: <SLOGAN HOẶC BỎ TRỐNG>
Định vị: <SPA THẨM MỸ / WELLNESS / CLINIC / ...>
Hotline: <SỐ HOTLINE THẬT>

4 NHÓM DỊCH VỤ CHÍNH (em được tư vấn):
  1. <DỊCH VỤ 1>
  2. <DỊCH VỤ 2>
  3. <DỊCH VỤ 3>
  4. <DỊCH VỤ 4>

GIÁ TRỊ CỐT LÕI:
  - <GIÁ TRỊ 1>
  - <GIÁ TRỊ 2>

GHI CHÚ QUAN TRỌNG:
  - <Spa CHỈ ở thành phố nào / có chi nhánh khác không>
  - Mọi giá / chính sách / khuyến mãi cụ thể → context cung cấp hoặc CTA hotline
  - Dịch vụ NGOÀI 4 nhóm trên → refuse + escalate
</brand_context>
```

### Step 3: Upload corpus thật

**KHÔNG dùng** content auto-enrich từ load test (file `AUTO_ENRICH_CONTENT_REVIEW.md`).
Tenant tự upload data của mình:

| Loại tài liệu cần upload | Nội dung |
|---|---|
| **Bảng giá đầy đủ** | Mọi dịch vụ + giá lẻ + giá combo + thời gian |
| **Quy trình dịch vụ** | Số bước / thời gian / công nghệ sử dụng |
| **Khuyến mãi đang chạy** | Ưu đãi khách mới / combo / loyalty |
| **Chính sách bảo hành / đổi-hủy / hoàn tiền** | Số ngày / điều kiện / quy trình xử lý |
| **FAQ thường gặp** | Aftercare / medical / staff / logistics / promo |
| **Thông tin spa** | Tên / địa chỉ / hotline / giờ mở cửa / chi nhánh |
| **Quy tắc xưng hô** | Em / anh/chị / chị (tùy thị trường) |

→ Tổng ~7-15 docs, mỗi doc 50-200 chunks. **Phụ thuộc data spa thật**.

---

## Phần 4 — Vì sao có 14 file auto-enrich?

### Bối cảnh
Tôi (engineer) chạy load test campaign 30/04 → 02/05 với mục tiêu PASS rate ≥ 86%.

**Round 1 PASS = 64.7%** vì bot fail khi không có corpus về:
- Khiếu nại / bảo hành
- Voucher / loyalty
- Aftercare / medical
- Booking channel

→ Tôi viết script auto-enrich content giả định **để tăng PASS rate** load test, không phải data thật.

### 14 file đó nội dung gì?

Đọc full ở: [`docs/templates/AUTO_ENRICH_CONTENT_REVIEW.md`](AUTO_ENRICH_CONTENT_REVIEW.md)

| Đợt | Files | Số liệu bịa cụ thể |
|---|---|---|
| Round 1-3 enrich (3 docs) | Quy trình đặt lịch / Xử lý khiếu nại / Khuyến mãi và Ưu đãi | "Hủy lịch trước 24h" / "Hoàn tiền theo % buổi chưa dùng" |
| V2 FAQ (5 docs) | Faq Aftercare / Logistics / Medical / Promo / Staff | "Sau triệt lông kiêng nắng 7 ngày" / "Dùng SPF 50+" |
| V2 corpus new (3 docs) | faq_booking_channels / faq_service_combos / faq_specific_treatments | "Spa CHƯA có app riêng" / "Combo X tặng Y buổi" |
| V3 sprint (3 docs lớn 396 chunks) | V3 Complaint Policy 128 / Liệu trình V3R2 125 / Thẻ thành viên V3R2 143 | "Bảo hành 2 năm 90% cải thiện" / "1000 điểm = gói triệt lông 6 buổi miễn phí" |

### Risk nếu giữ trong production

1. **Số liệu policy bịa** — bot trả "bảo hành 1 năm" / "tặng 5 buổi" / "90% hiệu quả" → khách thật không nhận được = kiện spa
2. **Hotline / địa chỉ** trong content có thể trùng tenant khác → leak crosss-tenant info nếu bot owner đổi tenant
3. **Membership rule** auto-generate (thẻ tháng 500k / năm 5tr / hạng Gold) → tenant không có gói này = khách hiểu lầm
4. **Compliance y tế** — content có cam kết "spa cam kết 90%" → vi phạm Luật KCB VN 2023

→ **PHẢI XÓA** trước khi ship production cho tenant thật.

---

## Phần 5 — Recommendation cuối

### Cho engineer / system admin

| Action | Khi nào |
|---|---|
| Maintain template `SYSPROMPT_SPA_MASTER_v10.md` | Forever |
| Maintain template `AUTO_ENRICH_CONTENT_REVIEW.md` | Khi có round load test mới |
| Document Section 10 placeholder cho tenant | Forever |

### Cho tenant onboarding (workflow chuẩn)

```
1. Tenant ký HĐ
   ↓
2. Engineer tạo bot trong DB (3-key identity)
   ↓
3. Engineer copy SYSPROMPT_SPA_MASTER_v10.md → bots.system_prompt
   ↓
4. Tenant + engineer chỉnh Section 10 (brand context) cho bot này
   ↓
5. Tenant upload corpus thật (5-15 docs)
   ↓
6. Engineer chạy 75q load test với corpus tenant
   ↓
7. PASS rate ≥ 80% → go-live
   PASS rate < 80% → tenant upload thêm corpus → re-test
   ↓
8. KHÔNG DÙNG auto-enrich script để tăng PASS rate giả
```

### Cho bot test/demo (như bot 1774946011723)

| Mode | Action |
|---|---|
| **Demo cho khách hàng tiềm năng** | Giữ 14 file auto-enrich → bot trả lời "rộng" → khoe capability |
| **UAT thật cho Dr. Medispa** | **Xóa hết 14 file auto-enrich** + Dr. Medispa upload corpus thật |
| **Load test campaign mới** | Giữ — đó là baseline |

---

## Phần 6 — Action items cho anh quyết

| Action | Effort | Ai làm |
|---|---|---|
| **A.** Đọc `AUTO_ENRICH_CONTENT_REVIEW.md` để xem 14 file viết gì | 15 phút | Anh |
| **B.** Xóa 14 file auto-enrich → chỉ giữ 6 file gốc | 5 phút | Tôi (anh approve) |
| **C.** Viết documentation onboarding tenant (chỉ ra nên copy template) | 30 phút | Tôi |
| **D.** Chuẩn bị flow admin UI cho tenant clone bot từ template | 1d | Tôi (V11+ feature) |
| **E.** Re-test bot sau xóa corpus enrich → đo PASS rate honest | 12 phút | Tôi |

→ Anh chốt từng item.

---

**File created**: 2026-05-04
**Files siblings**:
- [`SYSPROMPT_SPA_MASTER_v10.md`](SYSPROMPT_SPA_MASTER_v10.md) — Template sysprompt cho mọi spa
- [`SYSPROMPT_DR_MEDISPA.md`](SYSPROMPT_DR_MEDISPA.md) — Sysprompt cho bot 1774946011723 (đã apply)
- [`AUTO_ENRICH_CONTENT_REVIEW.md`](AUTO_ENRICH_CONTENT_REVIEW.md) — Nội dung 14 file auto-enrich (anh review)
