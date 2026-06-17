# Luồng hội thoại CHUẨN — 3 demo bot (multi-turn, như người thật)

> Mục đích: test luồng thật (coreference, multi-variant, booking state, OOS trap, aggregation)
> — KHÔNG phải câu factoid đơn lẻ. Mỗi flow = 1 cuộc trò chuyện liên tục (cùng `connect_id`).
> Cột "kỳ vọng" = hành vi đúng để chấm; "bẫy" = phải refuse honest (HALLU=0).

---

## 🧖 SPA — Dr. Medispa (`test-spa-id` / ws `spa`)

### Flow A — Khách mơ hồ → cụ thể → giá → đặt lịch (kịch bản tư vấn thật)
| # | Khách nói | Kỳ vọng bot |
|---|---|---|
| 1 | "chào em" | Chào lại, hỏi nhu cầu. KHÔNG xổ danh mục. |
| 2 | "bên em có dịch vụ gì cho da không ạ" | Hỏi lại tình trạng da **1 lần**, KHÔNG liệt kê + báo giá hàng loạt. |
| 3 | "da mình dạo này hay nổi mụn" | Map → Trị mụn; mô tả ngắn dịch vụ trị mụn (1 nhánh). |
| 4 | "giá nhiêu vậy em" | Coreference "nó"=trị mụn → **chỉ báo giá trị mụn** (299k ưu đãi/700k gốc). KHÔNG kèm dịch vụ khác. |
| 5 | "ok cho chị đặt lịch thử" | Vào booking: hỏi slot còn thiếu (tên/SĐT/thời gian). |
| 6 | "Hương, 0901234567" | Ghi nhận tên+SĐT, hỏi tiếp **thời gian** (KHÔNG hỏi lại tên/SĐT). |
| 7 | "chiều mai 3h nhé" | Đủ 4 slot → tóm tắt + chốt lịch bằng lời tự nhiên. |

### Flow B — Multi-variant cùng nhóm
| # | Khách nói | Kỳ vọng |
|---|---|---|
| 1 | "tẩy da chết giá bao nhiêu" | **Liệt kê ĐỦ 2 biến thể** cùng loại: tẩy da chết body (199k) + tẩy da chết & ủ trắng body (299k). KHÔNG kèm dịch vụ khác loại. |
| 2 | "cái ủ trắng đó làm bao lâu" | Coreference → đúng "tẩy da chết & ủ trắng body" (60 phút). |

### Flow C — Coreference quy trình
| # | Khách nói | Kỳ vọng |
|---|---|---|
| 1 | "massage cổ vai gáy thế nào" | Mô tả dịch vụ massage CVG (1 nhánh THÔNG TIN). |
| 2 | "quy trình gồm những gì" | Coreference → quy trình CHÍNH dịch vụ CVG, KHÔNG đổi dịch vụ. |

### Flow D — Bẫy OOS (phải refuse, HALLU=0)
| # | Khách nói | Kỳ vọng |
|---|---|---|
| 1 | "spa mình có bán mỹ phẩm mang về nhà không" | Nếu corpus không có → refuse honest ("chưa có thông tin... liên hệ hotline"), KHÔNG bịa. |
| 2 | "có dịch vụ phun xăm thẩm mỹ chứ" | Không có trong corpus → refuse honest, KHÔNG bịa giá/dịch vụ. |

---

## 🚗 XE — tra giá lốp (`chinh-sach-xe` / ws `xe`)

### Flow A — Multi-variant + follow-up
| # | Khách nói | Kỳ vọng |
|---|---|---|
| 1 | "cho hỏi lốp 265/50R20 giá nhiêu" | **Liệt kê ĐỦ mọi sản phẩm** khớp quy cách (CityTraxx H/P + WildTraxx A/T...), mỗi loại 1 dòng + giá + tồn kho. |
| 2 | "loại nào còn nhiều hàng hơn" | Coreference → so sánh tồn kho các loại vừa nêu (từ cột quantity). |
| 3 | "cái A/T đó còn mấy cái" | Coreference → đúng tồn kho WildTraxx A/T. |

### Flow B — Tồn kho = 0
| # | Khách nói | Kỳ vọng |
|---|---|---|
| 1 | "[quy cách có quantity=0]" | "hiện đang hết hàng" (đọc cột quantity, KHÔNG suy từ giá). |

### Flow C — Bẫy OOS
| # | Khách nói | Kỳ vọng |
|---|---|---|
| 1 | "xe sedan thì nên dùng lốp loại nào" | Corpus không có tư vấn-chọn-lốp → refuse honest, KHÔNG bịa khuyến nghị. |
| 2 | "lốp 999/99R99 giá nhiêu" | Quy cách không tồn tại → "chưa tìm thấy quy cách này". |

---

## 📜 LEGAL — Thông tư 09/2020/TT-NHNN (`thong-tu-09-2020-tt-nhnn` / ws `legal`)

### Flow A — Tra điều + coreference
| # | Khách nói | Kỳ vọng |
|---|---|---|
| 1 | "Điều 4 quy định về cái gì" | Trả nội dung Điều 4, cite Điều. |
| 2 | "khoản 2 của điều đó nói gì" | Coreference "điều đó"=Điều 4 → đúng Khoản 2 Điều 4. |
| 3 | "còn khoản 3 thì sao" | Coreference tiếp → Khoản 3 Điều 4. |

### Flow B — Tổng hợp (aggregation)
| # | Khách nói | Kỳ vọng |
|---|---|---|
| 1 | "thông tư này có mấy chương, mấy điều" | Đếm/tổng hợp từ tài liệu (tính trên dữ kiện thật, không bịa). |
| 2 | "áp dụng từ ngày nào" | Trả ngày hiệu lực từ tài liệu. |

### Flow C — Bẫy OOS
| # | Khách nói | Kỳ vọng |
|---|---|---|
| 1 | "luật doanh nghiệp 2020 quy định vốn điều lệ thế nào" | Khác văn bản → refuse honest ("không tìm thấy trong tài liệu"). |
| 2 | "Điều 999 nói gì" | Điều không tồn tại → refuse honest, KHÔNG bịa. |

---

## Cách chạy (multi-turn, cùng connect_id)
```python
# mỗi flow giữ 1 connect_id xuyên suốt để bot có history (coreference)
POST /api/ragbot/test/chat
  {bot_id, channel_type:"web", workspace_id, question, connect_id:"<flow-id>", bypass_cache:true}
```
**Chấm:** mỗi turn check (1) đúng dịch vụ/điều coreference, (2) không xổ list sai, (3) booking slot không hỏi lại, (4) bẫy OOS refuse honest = **HALLU=0**, (5) multi-variant liệt kê đủ.
