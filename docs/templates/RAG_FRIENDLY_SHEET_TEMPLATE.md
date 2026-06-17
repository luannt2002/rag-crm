# RAG-Friendly Document Template — Spec & Best Practice

> **Mindset shift (2026-05-06)**: customer giữ format gốc (table, header, paragraph). Template chỉ hướng dẫn **viết rõ ràng hơn** với 6 hint nhẹ. KHÔNG ép viết Q&A 4-cột thủ công — pipeline (Stream A doc-pipeline) đã đủ thông minh extract structure.
>
> **Sacred (CLAUDE.md)**: format này KHÔNG được thêm bất kỳ instruction nào CHO BOT (no anti-rules, no "must answer", no template injection). Bot owner viết content TỰ NHIÊN — format chỉ giúp embedding match tốt hơn + retrieval preserve section context.

---

## 1. Reality check — 3 tier customer doc

| Tier | Customer làm gì | Pipeline làm gì | PASS rate expect |
|---|---|---|---|
| **Tier 1 — Raw** | Upload nguyên file Word/Excel/Sheets/PDF | Pipeline tự extract structure (parser registry → row-as-chunk / heading split / table preserve) | **50–65%** |
| **Tier 2 — Styled** | Thêm header rõ + blank line + table header row + `Topic` column optional | Pipeline preserve structure + section context + Anthropic CR boost | **80–90%** |
| **Tier 3 — Q&A explicit** | Viết tay từng row Q+A 4-cột | Trivial chunk hóa | **95%+** |

→ **Recommend Tier 2** (sweet spot: customer chỉnh nhẹ, lift +20-25pp accuracy). Tier 3 unrealistic (customer không có thời gian rewrite 200 row).

---

## 2. Six light hints — R1 → R6

| Rule | Customer làm | Pipeline tận dụng (Stream A reference) |
|---|---|---|
| **R1** Header rõ ràng | `# Bảng giá` / `## Triệt lông` (markdown), bold title trong Word | Header-aware splitter (Phase 3) — H1 hard-break + parent_headings metadata |
| **R2** Blank line giữa topic | Enter 2 lần giữa các đoạn khác chủ đề | Recursive splitter primary `\n\n` separator |
| **R3** Sheet header row | Row 1 = column names (Tên, Giá, Vùng, Ghi chú) | Row-as-chunk + header propagate (Phase 1+2) — 1 row = 1 chunk preserved |
| **R4** Section divider | `---` markdown / page break Word / sheet riêng Excel | Hard break point |
| **R5** Large file split topic | >20K chars chia sub-section dưới `##` H2 hoặc tách sheet riêng | Chunker H1+H2 break, không cắt random |
| **R6** **Optional** Topic column | Sheet thêm cột `Topic` mô tả 30-80 ký tự / markdown thêm `<!-- topic: ... -->` HTML comment | Anthropic CR override (Phase 4.5) — customer Topic → metadata.enriched_prefix → embedding boost |

### R6 chi tiết — Anthropic Contextual Retrieval boost

```csv
Topic,Dich vu,Vung,Combo (VND),Khuyen mai
Bang gia triet long Diode Laser cho vung nho,Triet long Diode Laser,Mep,899.000,Mua 10 tang 5
Bang gia triet long Diode Laser cho vung mat,Triet long Diode Laser,Mat,1.499.000,Mua 10 tang 5
```

- KHÔNG bắt buộc — nếu thiếu, pipeline auto-gen bằng LLM `metadata_extraction_model`.
- KHÔNG được chứa instruction (`phải`, `must`, `không được`) — chỉ MÔ TẢ topic/section.
- Useful: customer dùng vocabulary chính xác của domain (synonym match cao hơn LLM auto-gen).
- Save cost: bot 10K chunk → save ~$0.10/upload (LLM enrichment skip).
- Header alternatives accent-insensitive: `Topic` / `Context` / `Section` / `Mô tả` / `Mo ta` / `Description`.

---

## 3. Format examples (concrete)

### Example A — Sheet bảng giá (Tier 2)

```csv
Topic,Dich vu,Vung,Combo 10 buoi (VND),Gia le (VND),Khuyen mai
Bang gia triet long Diode Laser cho vung nho,Triet long Diode Laser,Mep,899.000,129.000/buoi,Mua 10 tang 5
Bang gia triet long Diode Laser cho vung mat,Triet long Diode Laser,Mat,1.499.000,219.000/buoi,Mua 10 tang 5
Bang gia triet long Diode Laser cho vung nach,Triet long Diode Laser,Nach,1.199.000,199.000/buoi,Mua 10 tang 5
```

→ Pipeline (Stream A Phase 2): bypass smart_chunk → 1 row = 1 chunk + header context preserved trong metadata.

### Example B — File quy trình tư vấn (Tier 2)

```markdown
# Quy trình tư vấn

## Bước 1 — Chào khách

Nhân viên gọi tên khách + giới thiệu spa với 1 câu ngắn về dịch vụ chính.

## Bước 2 — Hỏi nhu cầu

Hỏi vùng cần làm + tình trạng da hiện tại + mục tiêu mong muốn.
```

→ Pipeline (Stream A Phase 3): header-aware chunker break tại `##` + propagate `parent_headings=["Quy trình tư vấn"]` vào metadata mỗi chunk.

### Example C — So sánh dịch vụ (Tier 2 với markdown table)

```markdown
## So sánh dịch vụ chăm sóc da

| Dịch vụ | Thời gian | Giá | Phù hợp |
|---|---|---|---|
| Cấp ẩm cơ bản | 60 phút | 700K | Da khô, mới làm quen |
| Hydrafacial | 75 phút | 1.2M | Da hỗn hợp + lỗ chân lông to |
| Mesotherapy | 90 phút | 2.5M | Da lão hóa, cần phục hồi sâu |
```

→ Markdown table preserved (tables are atomic blocks). Each row's content embedded với section header `## So sánh dịch vụ chăm sóc da`.

---

## 4. Sheets link path (post Stream A1)

```markdown
## Upload qua Google Sheets URL

1. Sheet phải `Anyone with link can view` (public share). Private OAuth defer Sprint 2.
2. URL format: `docs.google.com/spreadsheets/d/{id}/edit#gid={N}`
3. Pipeline tự fetch CSV export → parse như Excel (`GoogleSheetsParser` Phase 1 ship).
4. Sheet nhiều tab (gid khác) → upload từng URL, mỗi tab thành document riêng (giữ scope).

**Lưu ý**: private sheet OAuth chưa support — defer Sprint 2. MVP option: export `.xlsx` trực tiếp upload, hoặc copy data sang Google Sheets public.
```

---

## 5. Large file (>20K chars) strategy

```markdown
## File lớn — viết theo topic, không monolith

**Anti-pattern**: 1 file 27K chars "Quy trình tư vấn" 1 đoạn dài liền mạch.
**Pattern đúng**:
  - Chia thành H2 section: `## Bước 1 - Chào`, `## Bước 2 - Hỏi`, `## Bước 3 - Tư vấn`, ...
  - Mỗi section <2000 chars (chunker không cắt ngang section)
  - Hoặc tách thành nhiều sheet riêng theo topic (sheet1: quy trình, sheet2: bảng giá, sheet3: FAQ)
```

→ Pipeline (Stream A Phase 3) split tại heading boundary, không cắt ngẫu nhiên giữa flow.

---

## 6. Six anti-patterns CẤM trong format

| # | Anti-pattern | Vì sao | Fix |
|---|---|---|---|
| 1 | Inject instruction trong content ("Bot phải trả lời X") | LLM treat như user instruction → conflict với sysprompt → unpredictable | Content chỉ chứa fact; instruction để sysprompt |
| 2 | Pronoun mơ hồ ("cái đó", "mấy thứ này") | Chunk được retrieve độc lập → user nhận half answer | Tên dịch vụ + brand đầy đủ trong câu trả lời/row |
| 3 | Row >1024 chars | Sẽ bị cắt giữa row dù pipeline cố preserve | Tách thành 2-3 row Q&A riêng |
| 4 | Duplicate sheet 99% | Embedding noise, lower top_score | 1 sheet duy nhất, sheet khác link qua tags/topic |
| 5 | Marketing fluff đầu paragraph | Đẩy fact ra cuối → top_score thấp | Fact đầu, marketing tách sang sheet "Chi tiết" riêng |
| 6 | Topic column (R6) chứa rule cho bot ("phải refuse khi...") | Topic = mô tả data, KHÔNG phải instruction | Topic chỉ describe chunk topic, không direct LLM behaviour |

---

## 7. Pre-upload self-check (7 items)

```
[ ] R1: Header rõ (`# H1`, `## H2`)?
[ ] R2: Blank line giữa các topic khác nhau?
[ ] R3: Sheet có header row làm column names?
[ ] R4: Section divider (`---` / sheet riêng / page break)?
[ ] R5: File >20K chars đã chia H2/section?
[ ] R6 (optional): Topic column 30-80 chars KHÔNG chứa rule?
[ ] KHÔNG anti-pattern 1-6?
```

Run pipeline smoke test sau upload:
```sql
-- Verify chunks ingested với section context
SELECT
  count(*) as n_chunks,
  count(metadata_json->>'parent_headings') as n_with_section_context,
  count(metadata_json->>'enriched_prefix') as n_with_topic_prefix
FROM document_chunks
WHERE record_bot_id = '<your-bot-uuid>';
```

Expect: `n_with_section_context >= 80%` of chunks (markdown / Word with headers), `n_with_topic_prefix >= 50%` if customer used R6 Topic column.

---

## 8. Expected metrics (post Stream A pipeline + customer Tier 2)

| Metric | Tier 1 raw (V13 baseline) | Tier 2 styled (post template) | Lift |
|---|---|---|---|
| PASS rate baseline 75 | 65% raw answer rate | **85-90%** expect | +20-25pp |
| Top_score median | 0.27 | **0.50+** | ~2× |
| Over-refuse rate | 16/75 = 21% | **<8%** | -13pp |
| HALLU=0 sacred | 15-round streak | **hold** | (sacred) |

→ Verify post-deploy bằng `python scripts/agent_d_loadtest.py` 90Q + `python scripts/reclassify_loadtest.py` (Stream F).

---

## 9. Reference

- Anthropic Contextual Retrieval (paper #12 APPLIED-DONE): pattern `enriched_prefix` per-chunk + R6 customer override path
- Anthropic XML prompt principles (paper #07 APPLIED-DONE): citation format
- CRAG (paper #03 APPLIED-DONE): grade chunk + retry — sysprompt LOW SCORE 0.15-0.40 logic ground vào CRAG
- Stream A Phase 1 — `GoogleSheetsParser` CSV export bytes → row-as-chunk
- Stream A Phase 2 — preserve parser row-chunks (G2 root cause V13 over-refuse)
- Stream A Phase 3 — H1 hard-break + parent_headings metadata
- Stream A Phase 4.5 — customer Topic column → enriched_prefix
- Sysprompt template generic: `docs/templates/SYSPROMPT_TEMPLATE.md`
- Industry skeletons: `docs/templates/sysprompt_examples/`
- Test result: `reports/LOADTEST_90Q_FULLMINI_REPORT_20260506.md`

---

**Last updated**: 2026-05-06
**Status**: rewrite Tier 1→2 mindset. Validation pending — sẽ verify sau rerun 90Q với corpus mới + Stream A Phase 1-3 ship live.
