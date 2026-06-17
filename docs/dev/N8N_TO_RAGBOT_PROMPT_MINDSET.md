# Chuyển hoá prompt n8n → ragbot — Mindset & Guide

> Đúc kết từ 15 prompt n8n thật (~11 doanh nghiệp: Beespace, UP Garden, KDExpress,
> Beetech, DAO Carton, CBL, Quang Phúc, CENTREC, Dr. Medispa, Fine Mold...) trong
> `z-luannt-prompt-n8n.txtx`. Mục đích: convert "văn hoá viết prompt n8n" sang
> ragbot để support chuyên gia — KHÔNG vi phạm sacred rule (domain-neutral,
> no app-inject, HALLU=0).

---

## 1. Văn hoá prompt n8n (quan sát được)

| Đặc trưng | Mô tả | Ví dụ |
|---|---|---|
| **State-machine** | Flow `BƯỚC 1→9` + `NHÁNH A/B`, greeting→qualify→answer→handoff | Beetech IT-Comtor, Dr.Medispa `origin_intent`/`booking_confirmed` |
| **Data nhúng INLINE** | Giá/địa chỉ/FAQ baked thẳng vào prompt | KDExpress KB, 24 script giá Dr.Medispa |
| **Hard-rule nặng** | `BẮT BUỘC`/`CẤM TUYỆT ĐỐI` lặp 3-6 lần | Beespace special-date lặp 8× |
| **Sales > Q&A** | Mục tiêu = thu SĐT/booking, chặn câu trả lời sau lead-capture | Beetech, Beespace |
| **Format cứng** | "1 câu/lượt", cấm markdown/emoji, cap độ dài | Quang Phúc 2-3 dòng, CENTREC <30 từ |
| **Arithmetic trong prompt** | Bắt LLM tính diện tích/VAT/tổng tiền | Quang Phúc `IF(D>8 AND R>8)→STOP` |
| **Anti-hallu** | "chỉ từ tài liệu, không bịa", nhãn `[Chưa xác minh]` | giá trị chung mạnh nhất |

---

## 2. LUẬT CHUYỂN HOÁ — tách 6 tầng (cốt lõi)

**Mỗi thứ trong prompt n8n PHẢI rơi đúng 1 trong 6 tầng ragbot:**

| Thói quen n8n | → Tầng ragbot | Lý do |
|---|---|---|
| Persona / tone / hình-dạng-flow (chào→hỏi→đáp) | `bots.system_prompt` | Owner content, Tier-1 |
| **Data thực** (giá, địa chỉ, mô tả, FAQ) | → **CORPUS** (retrieve) | Không baked → cập nhật được, retrieve được, không stale |
| **Số liệu sống** (tồn kho, tổng tiền, "đắt nhất", "còn hàng") | → **STRUCTURED-DATA** (stats/record route) | Vector không làm toán; SQL deterministic |
| Refusal text + toggle (khoá Tết, ngày đặc biệt, "Office no price") | → **CONFIG** (`oos_answer_template`, `plan_limits`) | Đổi behavior không sửa prompt |
| Arithmetic + booking slot-fill | → **action-framework** (defer) | HALLU vector nếu để LLM tính |
| Verbatim "copy câu này y nguyên" | → **BỎ HẲN** | Vi phạm sacred #10 + trip `system_leak` shingle |
| "Chỉ từ data, không bịa" | → **đã có sẵn** (grounding/HALLU=0) | Đừng lặp lại tường rule trong prompt |

---

## 3. Mechanism "thêm support chuyên gia" cho MỌI bot

Văn hoá n8n hay = các **hard-rule lặp lại** (cách đọc bảng, chống nhầm dòng, slot-capture).
ragbot đã có **`SysPromptAssembler`** (`application/services/sysprompt_assembler.py`) đúng để đựng cái đó:

```
Final prompt LLM thấy =
   bots.system_prompt                                    (Tier-1: owner)
 + language_packs[locale].sysprompt_default_rules        (Tier-6: platform default)
 − plan_limits["sysprompt_rules_disabled"]               (opt-out per-bot)
```

→ **Đúc các hard-rule chung của n8n thành platform-default rules** (domain-neutral, governed
qua alembic, opt-out per-bot). 1 chỗ → mọi bot hưởng. Đây là cách "support chuyên gia" mà
KHÔNG hardcode per-bot, KHÔNG inject answer.

**Quy tắc viết platform-default rule** (governed, ADR-W1-S10):
- APPEND only (không prepend/chèn giữa).
- Domain-neutral (không tên brand/ngành).
- Text qua alembic tracked (cấm psql hot-fix).
- Có pin test (`tests/unit/test_sysprompt_assembler_pin.py`).
- Owner xem được prompt cuối qua `GET /admin/bots/{id}/effective-prompt`.

---

## 4. Template viết `bots.system_prompt` mới (chuẩn ragbot)

```
# <Vai trò 1 câu> — <persona/tone>

## Phong cách
- <giọng điệu, độ dài, xưng hô>. KHÔNG markdown nếu kênh không hỗ trợ.

## Luồng tư vấn (hình dạng, KHÔNG nhúng data)
- Chào → làm rõ nhu cầu → trả lời từ tài liệu → mời bước tiếp.
- Nếu thiếu thông tin để trả lời → hỏi lại đúng 1 ý.

## Ranh giới
- Chỉ trả lời từ ngữ cảnh được cung cấp. Không có thông tin → nói chưa có +
  mời để lại liên hệ. (KHÔNG tự bịa số/giá/tồn kho.)
- KHÔNG tự tính toán con số; số đến từ hệ thống.

## (KHÔNG viết ở đây: bảng giá, địa chỉ, FAQ, câu mẫu verbatim — chúng ở corpus/config)
```

So sánh với prompt n8n: **bỏ data inline, bỏ verbatim template, bỏ arithmetic, bỏ tường
hard-rule lặp** → còn lại persona + flow-shape + ranh giới. Phần "nặng" chuyển sang
corpus/structured/config/platform-rules.

---

## 5. Checklist convert 1 bot n8n → ragbot

1. [ ] Tách data inline → upload thành **corpus** (sheet/doc).
2. [ ] Số liệu sống (giá/tồn/tổng) → **structured index** (`document_service_index` + record route).
3. [ ] Refusal/toggle → `bots.oos_answer_template` + `plan_limits`.
4. [ ] Arithmetic/booking → defer action-framework.
5. [ ] Verbatim template → **xoá**.
6. [ ] Hard-rule chung → đề xuất thành **platform-default rule** (nếu domain-neutral).
7. [ ] Còn lại (persona+flow) → `bots.system_prompt` theo template §4.
8. [ ] Golden set per-bot → đo coverage + HALLU=0 (rule#0).
