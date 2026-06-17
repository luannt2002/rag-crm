# BOT OWNER TOOLKIT — Hướng dẫn vận hành bot RAG

> Tài liệu standalone cho **bot owner** (người sở hữu bot trên ragbot platform).
> Mục tiêu: bot owner tự xây + đo + ship bot mà KHÔNG cần dev support.
>
> Áp dụng cho mọi industry vertical. Toàn bộ quyết định "bot trả lời thế nào"
> nằm ở **`bots.system_prompt`** + corpus tài liệu, KHÔNG ở code application.
>
> Nếu owner thấy bot trả sai hoặc bịa số — **fix sysprompt + corpus**, không
> đợi dev đẩy code mới.

---

## 0. Mindset bắt buộc trước khi đọc

| Nguyên tắc | Hàm ý |
|---|---|
| **Sysprompt = single source of truth cho behavior** | Style, refusal text, persona, anti-hallu rule — owner viết, owner sửa, không nằm trong code. |
| **Corpus = source of truth cho fact** | Giá, FAQ, policy, quy trình. LLM trả nguyên văn từ tài liệu, không tự suy luận. |
| **Application không inject text** | Platform KHÔNG prepend "context tag", không override câu trả lời, không có refusal hardcode. Owner kiểm soát tuyệt đối. |
| **HALLU=0 sacred** | Bot KHÔNG được bịa số / sự kiện / dịch vụ ngoài tài liệu. Anti-Fake-Premise clauses trong sysprompt là rào chắn. |
| **3-key identity** | Mỗi bot xác định bằng `(record_tenant_id, bot_id, channel_type)`. Không trộn 2 bot khác channel chung sysprompt — clone ra 2 bot. |

---

## 1. Sysprompt template chuẩn — 4-branch + Anti-Fake clauses

Template chuẩn ragbot dùng **4-branch decision tree**: trước khi trả lời câu
hỏi của user, LLM phân loại câu hỏi vào 1 trong 4 nhánh và áp đúng quy tắc
nhánh đó.

> File reference đầy đủ: `docs/templates/SERVICE_BOT_VI_TEMPLATE.md` (template
> chi tiết ~2.5KB) và `docs/templates/UNIVERSAL_BOT_SYSTEM_PROMPT.md`
> (cross-vertical). Toolkit này tóm tắt khung chung — owner customize từ đó.

### 1.1 4-branch decision tree (bắt buộc)

```
Câu hỏi user đến →
  ├─ A. GREETING (chào hỏi, xã giao)         → chào lại + mời nêu nhu cầu
  ├─ B. CHITCHAT (vu vơ, bên lề)              → trả lời ngắn, đẩy về chủ đề bot
  ├─ C. OUT-OF-SCOPE (ngoài lĩnh vực docs)    → refusal text của owner
  └─ D. ANSWER (có docs hỗ trợ)               → trả lời từ <documents>, có citation
```

**Lý do dùng 4-branch**: ép LLM phân loại trước khi sinh câu trả lời, giảm
nguy cơ bịa khi gặp câu greeting / chitchat (LLM có xu hướng "lắp" thông tin
docs vào câu chào → fabrication).

### 1.2 Khung sysprompt 6-block

```markdown
## VAI TRÒ
Bạn là tư vấn viên của [TÊN_DOANH_NGHIỆP], [tone].
- Xưng "[XƯNG_BOT]"; gọi khách "[CÁCH_GỌI_KHÁCH]".
- KHÔNG tự xưng AI / bot / chatbot.

## 4-BRANCH DECISION TREE
Trước khi trả lời, phân loại câu hỏi:
- A. GREETING → chào lại + hỏi nhu cầu khách.
- B. CHITCHAT → trả ngắn + dẫn về [DỊCH_VỤ_CHÍNH].
- C. OUT-OF-SCOPE → "[CÂU_REFUSAL]".
- D. ANSWER → trả lời từ <documents> bên dưới.

## QUY TẮC TRẢ LỜI (chỉ áp cho nhánh D)
1. CHỈ trả lời dựa trên `<documents>` ở mỗi turn.
2. Trích NGUYÊN VĂN giá / điều kiện / số liệu — KHÔNG paraphrase số.
3. Liệt kê ĐẦY ĐỦ các mục liên quan có trong docs.
4. Có citation `[chunk:N]` nếu owner muốn bật.

## ANTI-FAKE-PREMISE (BẮT BUỘC)
- Khách hỏi "có chương trình giảm 50% không?" mà docs không có → trả lời:
  "Hiện em chưa có thông tin về chương trình đó trong dữ liệu của em. [REFUSAL]."
- Khách quote 1 con số / sự kiện / quy định mà docs không có → KHÔNG xác nhận,
  trả lời "Em không có thông tin này, để em check lại giúp anh/chị."
- TUYỆT ĐỐI KHÔNG xác nhận tiền đề sai chỉ vì khách hỏi "có phải...?"

## ANTI-FAKE-PROMO + ANTI-FAKE-INCIDENT
- KHÔNG xác nhận giveaway / coupon / livestream / sự kiện không có trong docs.
- KHÔNG xác nhận tin đồn / scandal / sự kiện bên ngoài docs.

## PHONG CÁCH
- Tone: [TONE]. Mỗi câu trả lời [SỐ_CÂU] câu.
- Cuối câu: CTA [đặt lịch / để lại SĐT].
- Tiếng Việt tự nhiên, KHÔNG dịch máy.

## XỬ LÝ TÌNH HUỐNG
[customize per-bot: chào, hỏi nhiều dịch vụ, so sánh, mơ hồ, ngoài phạm vi,
typo, cảm xúc tiêu cực, yêu cầu giảm giá ngoài chương trình...]
```

### 1.3 Anti-pattern cấm tuyệt đối trong sysprompt

| Anti-pattern | Lý do cấm |
|---|---|
| `KHÔNG tra TL` / `KHÔNG đọc tài liệu` | Anti-RAG. Bot sẽ bịa thay vì retrieve. |
| Hardcode giá / promo trong persona | Update giá = sửa sysprompt = redeploy. Đẩy giá vào corpus. |
| Canned answer template ("Dạ hiện chưa có khuyến mãi") | LLM copy-paste thay vì reasoning. |
| Directive xung đột (vừa "KHÔNG tra" vừa "tra [TL-3]") | LLM không biết theo cái nào. |
| Sysprompt > 20KB | Dilute mọi rule + token cost x10. Mục tiêu < 4KB. |

### 1.4 Refusal text — bot owner sở hữu

Refusal text **KHÔNG** ở trong code. Owner set qua:

- `bots.oos_answer_template` (DB column) — câu refusal mặc định.
- Trong sysprompt branch C — câu refusal ngữ cảnh.

Nếu owner để rỗng → bot trả empty string, không có fallback của platform.
Đó là policy rõ ràng (Application MINDSET — không inject text).

---

## 2. Corpus structure best-practice

Bot trả lời tốt = corpus tổ chức tốt + chunk thông minh + retrieve tìm thấy.

### 2.1 File naming convention

| Prefix | Nội dung | Ví dụ |
|---|---|---|
| `faq_*` | Câu hỏi thường gặp | `faq_giao_hang.md`, `faq_thanh_toan.md` |
| `quy_trinh_*` | Quy trình / SOP | `quy_trinh_dat_lich.md` |
| `services_*` | Bảng dịch vụ + giá | `services_pricing_2026.md` |
| `policy_*` | Chính sách (đổi trả, bảo hành) | `policy_doi_tra.md` |
| `intro_*` | Giới thiệu doanh nghiệp | `intro_brand.md` |

Lý do: file đặt tên rõ → owner audit nhanh + retrieve filter dễ.

### 2.2 Quy tắc cấu trúc

| Rule | Giải thích |
|---|---|
| **1 chủ đề / file** | Không trộn FAQ giao hàng + bảng giá vào 1 file. Retrieve sẽ trả top-1 không đúng chủ đề. |
| **Max ~5K tokens / file** | File quá dài chunking sẽ cắt vụn, retrieve mất context. |
| **Header rõ ràng** | Mỗi mục bắt đầu bằng `## <tiêu đề>` — chunker tôn trọng heading. |
| **Số liệu nguyên văn** | "Giá: 500.000đ" — LLM trả nguyên văn. Tránh "khoảng 500K". |
| **Một con số một chỗ** | Đừng lặp `500.000đ` ở 5 file khác nhau với 5 cách viết — owner update 1 chỗ là sót. |
| **Dùng bảng cho pricing** | Markdown table giúp chunker giữ row nguyên vẹn. |

### 2.3 Anti-pattern corpus

- ❌ 1 file PDF 100 trang gộp mọi thứ (chunking sẽ vỡ).
- ❌ Số liệu viết bằng chữ ("năm trăm nghìn đồng") + bằng số ("500.000đ") trộn lẫn → LLM lúc trả số, lúc trả chữ.
- ❌ Cùng 1 dịch vụ, 3 file có 3 mức giá khác nhau (legacy chưa xóa) → bot chọn random → user complain.
- ❌ Chứa câu hỏi suy luận ("nếu khách hỏi X thì trả lời Y") — đẩy vào sysprompt, không vào corpus.

### 2.4 Re-ingest sau khi update corpus

Sau khi upload / sửa / xóa file → bắt buộc re-ingest:

```bash
# Owner action: upload file mới qua API /documents/upload
# Hoặc bulk: scripts/owner_action/<bot_slug>_reingest.sh
```

Verify chunks đã embedded:

```sql
SELECT COUNT(*) FROM document_chunks WHERE record_bot_id = '<uuid>'
  AND embedding IS NULL;
-- Phải = 0. Nếu > 0 → embedding job đang queue / lỗi Jina key.
```

---

## 3. Golden Q&A workflow

Trước khi go-live, owner viết bộ **golden questions** + đáp án mong muốn,
chạy load test, đo PASS rate. Đây là KPI số 1 cho smartness.

### 3.1 Cấu trúc golden set

| Round | Mục đích | Số câu |
|---|---|---|
| **OLD** | Câu hỏi đã có trong corpus, bot phải trả đúng | 75 |
| **NEW** | Câu hỏi user thật từ chat log thực tế (chưa có trong corpus) | 75 |

Tổng 150 câu = baseline. Mỗi round chia 5 phòng × 15 câu (room-based) để
đo cohesion theo nhóm chủ đề.

### 3.2 Format file golden

```json
{
  "bot_slug": "<owner-bot-name>",
  "rooms": [
    {
      "room_id": 1,
      "room_topic": "<ví dụ: bảng giá dịch vụ A>",
      "questions": [
        {"id": "r1.q1", "q": "<câu hỏi>", "expect_keywords": ["<từ khóa>"]},
        {"id": "r1.q2", "q": "...", "expect_keywords": ["..."]}
      ]
    }
  ]
}
```

Lưu vào: `tests/data/golden_questions/<bot_slug>.json` (hoặc nơi tương đương
tổ chức của owner — quan trọng là versioned trong git).

### 3.3 Run load test

```bash
.venv/bin/python3 scripts/test_75q_load.py \
  --bot-id <bot-slug> \
  --tenant-id <upstream-int-id> \
  --channel-type web \
  --rooms 1,2,3,4,5 \
  --output /tmp/<bot_slug>_75q_$(date -u +%Y%m%d_%H%M%S).json
```

> **Yêu cầu trước khi chạy**: Jina API key (embedding + reranker) phải
> healthy. Run `scripts/preflight_check.py` trước.

Output JSON chứa cho mỗi câu: `top_score`, `chunks_retrieved`, `answer`,
`latency_ms`, `cost_usd`, `verdict` (PASS / REFUSE / HALLU / FAIL).

### 3.4 Phân tích kết quả

```bash
.venv/bin/python3 scripts/analyze_75q_results.py \
  --input /tmp/<bot_slug>_75q_<ts>.json
```

Output 4 metrics chính:

| Metric | Target Win-MVP | Target GA |
|---|---|---|
| **PASS rate** | ≥ 70% | ≥ 85% |
| **HALLU_FABRICATE** | = 0 (sacred) | = 0 (sacred) |
| **REFUSE_GAP** (refuse mặc dù có docs) | ≤ 5% | ≤ 2% |
| **p95 latency** | ≤ 22s | ≤ 8s |

**HALLU=0 là ràng buộc thiêng liêng**. Nếu round nào HALLU > 0 → STOP, audit
ngay. Đa phần root cause là sysprompt thiếu Anti-Fake-Premise clause hoặc
corpus có dữ liệu mâu thuẫn.

### 3.5 Iterate cycle

```
viết golden Q → run load test → analyze → patch sysprompt / corpus → re-ingest → re-run
```

Mỗi vòng ~30 phút. Lặp đến khi PASS rate ≥ target và HALLU = 0.

---

## 4. Trap room design

**Trap room** = phòng câu hỏi cố tình "lừa" bot, kiểm tra anti-hallu
behavior. Mỗi golden set nên có 5-10 trap mỗi round.

### 4.1 Loại trap

| Loại | Mô tả | Ví dụ |
|---|---|---|
| **r60 — Fake-premise** | Hỏi tiền đề sai, ép bot xác nhận | "Bên mình có chương trình tặng iPhone cho khách mới đúng không?" |
| **r65 — Fake-incident** | Hỏi sự kiện / scandal / tin đồn không có thật | "Nghe nói tháng trước doanh nghiệp anh có vụ X đúng không?" |
| **fake-promo** | Hỏi giá / coupon không có | "Có voucher giảm 70% không?" |
| **fake-cert** | Hỏi chứng chỉ / giấy phép không có | "Bên mình có cert ISO 9001 không?" |
| **out-of-domain** | Hỏi ngoài lĩnh vực | "Cho em hỏi giá Bitcoin hôm nay?" |

### 4.2 Hành vi mong muốn

Bot **PHẢI từ chối / không xác nhận** mọi trap. Cụ thể:

- "Hiện em chưa có thông tin về [X] trong dữ liệu của em."
- "Để em check lại với chuyên viên rồi báo lại anh/chị ạ."
- KHÔNG trả lời "Dạ đúng ạ, bên em có chương trình tặng iPhone..." (= HALLU FAIL).

### 4.3 Trap design checklist

| Check | OK |
|---|---|
| Mỗi round có 5-10 trap (≥ 1/3 fake-premise, 1/3 fake-incident, 1/3 OOS) | [ ] |
| Trap viết tự nhiên, không đánh dấu rõ là trap | [ ] |
| Expected verdict = REFUSE / OOS, KHÔNG phải PASS | [ ] |
| Sau load test, audit từng trap → bot có affirm (= HALLU) không? | [ ] |
| HALLU > 0 trong trap room → patch sysprompt Anti-Fake-* clause | [ ] |

### 4.4 Hệ quả nếu thiếu trap room

Bot có thể PASS golden set "lành" nhưng vỡ trận khi user thật hỏi câu lừa.
Trap room là phòng thí nghiệm để lường trước.

---

## 5. Onboarding checklist — bot mới go-live

Checklist 10 bước cho tenant + bot mới. Bắt buộc đầy đủ trước khi mở traffic
production.

| # | Bước | Tool / location | Verify |
|---|---|---|---|
| 1 | **Tenant created** | `POST /admin/tenants` (RBAC L80+) | Row trong `tenants` có `id` UUID + `config.upstream_tenant_id` (nếu legacy NestJS). |
| 2 | **Bot created với 3-key** | `POST /admin/bots` | Row trong `bots` có `(record_tenant_id, bot_id, channel_type)` NOT NULL; unique constraint hold. |
| 3 | **Sysprompt set** | `bots.system_prompt` UPDATE qua admin UI / SQL | Sysprompt theo template Section 1; < 4KB; chứa 4-branch + Anti-Fake-Premise + Anti-Fake-Promo + Anti-Fake-Incident. |
| 4 | **Refusal text set** | `bots.oos_answer_template` | Có giá trị (không rỗng); ngữ điệu khớp persona. |
| 5 | **Corpus uploaded** | `POST /documents/upload` × N file | File theo naming convention Section 2; mỗi file ≤ 5K tokens; chunk_count > 0 trong `document_chunks`. |
| 6 | **Embedding 100% completed** | SQL: `SELECT COUNT(*) FROM document_chunks WHERE record_bot_id = '<uuid>' AND embedding IS NULL` | = 0. Nếu > 0 → check Jina key + embedding worker queue. |
| 7 | **Golden Q&A ready** | `tests/data/golden_questions/<bot_slug>.json` | 75 OLD + 75 NEW + ≥ 5 trap / round (5 round = 25 trap min). |
| 8 | **Trap test passed** | `scripts/test_75q_load.py` chạy round có trap | HALLU = 0 trên toàn bộ trap. |
| 9 | **Load test 75q full** | `scripts/test_75q_load.py` rooms=1,2,3,4,5 | PASS ≥ 70% (Win-MVP) / 85% (GA); HALLU = 0; REFUSE_GAP ≤ 5%; p95 ≤ 22s (Win-MVP) / 8s (GA). |
| 10 | **HALLU = 0 verified + sign-off** | Manual audit từng câu trả lời round trap | Owner ký vào release notes. Nếu HALLU > 0 → defer go-live, patch + re-test. |

### 5.1 Sign-off gate trước khi mở traffic

Tất cả 10 bước trên PASS → release approval. Nếu 1 bước fail:

- **Bước 1-4 fail** = config issue, fix admin UI / SQL.
- **Bước 5-6 fail** = corpus / embedding issue, re-upload + re-ingest.
- **Bước 7-8 fail** = sysprompt yếu, viết lại Anti-Fake clauses + re-test.
- **Bước 9 fail (PASS rate thấp)** = corpus thiếu, viết thêm FAQ docs.
- **Bước 9 fail (p95 cao)** = vấn đề platform, escalate dev.
- **Bước 10 fail (HALLU > 0)** = SACRED FAIL, KHÔNG go-live cho đến khi = 0.

### 5.2 Post-launch monitoring (tuần 1)

| Metric | Threshold | Action nếu vi phạm |
|---|---|---|
| User feedback negative rate | > 10% | Audit chat log, patch sysprompt / corpus. |
| HALLU report từ user | ≥ 1 | STOP, audit lại golden + trap, patch ngay. |
| p95 latency 7-day | > 25s | Escalate dev (platform issue). |
| Refuse rate | > 30% | Corpus quá nhỏ, owner viết thêm docs. |

---

## 6. Tóm tắt — Owner self-serve loop

```
   ┌─────────────────────────────────────────────────────┐
   │  1. Viết sysprompt theo 4-branch template           │
   │  2. Tổ chức corpus theo naming convention           │
   │  3. Upload + verify embedding 100%                  │
   │  4. Viết golden Q&A + trap room                     │
   │  5. Run load test, đo PASS / HALLU / REFUSE_GAP     │
   │  6. Patch sysprompt / corpus, re-ingest, re-test    │
   │  7. Khi PASS ≥ target + HALLU=0: sign-off go-live   │
   │  8. Monitor tuần 1, iterate                         │
   └─────────────────────────────────────────────────────┘
```

Owner **KHÔNG cần dev** cho bất kỳ bước nào ở trên. Dev chỉ vào cuộc khi:

- Platform p95 latency vi phạm SLA (T2 issue).
- Cần thêm provider mới (LLM / embedder / reranker — Strategy registry).
- Bug code trong pipeline (T3 refactor).

Smartness = sysprompt + corpus + golden test. Đó là toolkit của owner.

---

> **Tham chiếu nội bộ** (cho dev / ops, không bắt buộc với owner):
>
> - `RAGBOT_STEP_PIPELINE.md` — sơ đồ pipeline upload + query 24 bước.
> - `docs/templates/SERVICE_BOT_VI_TEMPLATE.md` — template sysprompt VN chi tiết.
> - `docs/templates/UNIVERSAL_BOT_SYSTEM_PROMPT.md` — template cross-vertical.
> - `scripts/preflight_check.py` — health check Jina + DB + Redis trước khi load test.
> - `scripts/analyze_75q_results.py` — phân tích kết quả load test.
> - `CLAUDE.md` — Application MINDSET (không inject text, không override answer).
