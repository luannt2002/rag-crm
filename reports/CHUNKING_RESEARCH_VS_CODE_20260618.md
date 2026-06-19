# Chunking Research ⟷ Code — Đối chiếu (2026-06-18)

> READ-ONLY report. KHÔNG sửa `src/`. Mục đích: map các luận điểm của nghiên cứu chunking (bản
> tổng hợp tiếng Việt + Firecrawl 2026 + EvidentlyAI RAG-eval) vào code THẬT của Ragbot, xác nhận
> chỗ đã đúng (giữ — strangler fig) và liệt kê gap (ứng viên, lift CHƯA đo).
>
> Evidence convention: mọi dòng code-claim trỏ `file:line` hoặc trỏ report đã verified
> ([DEEPDIVE_CHUNKING_20260617.md], [PROJECT_ALL_FLOWS_20260618.md]). Nhãn **SỰ THẬT** = có evidence
> kiểm chứng; **GIẢ THUYẾT** = chưa đo trên corpus của mình.

---

## 1. Nguồn đã đọc

| Nguồn | Trạng thái |
|---|---|
| Research tổng hợp (10 phần: paradox 3-chiều, early/late/semantic, VN-specific, eval 4-trụ) | Đã đọc (bản dán = nội dung studocu) |
| Firecrawl — *Best Chunking Strategies 2026* | Đã fetch |
| EvidentlyAI — *RAG evaluation* | Đã fetch |
| unstructured.io · viblo · studocu (URL gốc) | CHƯA fetch (studocu = bản dán) |
| `reports/DEEPDIVE_CHUNKING_20260617.md` | Đã đọc (nguồn file:line chunking) |
| `reports/PROJECT_ALL_FLOWS_20260618.md` §0–§9 + PART II-A→C | Đã đọc |

---

## 2. Luận điểm nền của research ⟷ bug đo được của mình

Research (Phần 2) đặt **nghịch lý 3-chiều** mà mọi chiến lược chunking phải giải:

| Research | Định nghĩa | Biểu hiện trong code mình (evidence) |
|---|---|---|
| **Noise Problem** | chunk to → vector nén nhiều ý → tín hiệu 1 entity bị pha loãng | **🚨 BUG-1 CONFLATE giá** — `table_dual_index` emit GROUP chunk đa-dịch-vụ → *"embedding centroid lẫn"* → vector kéo nhầm giá. PROJECT_ALL_FLOWS §6 root-cause (L336-337) + §9-A chain (L428-430). **SỰ THẬT** (load-test §0: factoid-giá trả sai entity, đo brittleness 6 cách hỏi → 6 đáp án). |
| **Context Problem** | chunk quá nhỏ → mất luồng, orphan ngữ cảnh | Semantic-chunk "vỡ vụn" research đo Doc-F1 0.42 @ ~43 tok. Code mình có `semantic` strategy nhưng **gated, không default** — `select_strategy` rule-scorer ưu tiên `recursive`/`hdt` (DEEPDIVE §1 L33). → mình tránh được bẫy này by-design. |
| **Cost / Context-window** | nhiều chunk → nhiều embed call + giới hạn token model | Late-chunking ON để giảm cost re-embed; embed batch 50 (PROJECT_ALL_FLOWS §6 U7, L326). |

**Kết luận §2:** BUG-1 của mình **chính là Noise Problem ở dạng cụ thể** trên corpus dịch vụ — không phải lỗi mới lạ. Research độc lập xác nhận hướng fix đã ghi trong DEEPDIVE §3 là đúng tầng (retrieval/ingest, không phải sysprompt).

---

## 3. Research XÁC NHẬN mình đã đúng — GIỮ (đập = vi phạm strangler-fig)

| Research khuyến nghị | Code mình ĐÃ CÓ | Evidence |
|---|---|---|
| Recursive 400–512 tok, overlap 10–20% = **default an toàn nhất** | strategy `recursive` + `DEFAULT_CHUNK_SIZE/OVERLAP` | PROJECT_ALL_FLOWS §3 (L223); `select_strategy` → recursive/hdt (DEEPDIVE §1 L33) |
| VN: **word-segmentation TRƯỚC BPE** (bắt buộc bảo vệ từ ghép) | U6 `segment_vi_compounds` → `content_segmented` | PROJECT_ALL_FLOWS §6 U6 (L325); `vi_tokenizer.py` |
| VN pháp lý: **section-split Điều/Khoản/Điểm** + micro-headers metadata | HDT breadcrumb `[Chương > Mục > Điều]` prefix — verified working | DEEPDIVE §1 (L38) `strategies.py:_chunk_hdt` 277-357 |
| Bảo toàn bảng > nhồi token cứng | `table_csv` row-as-chunk + header-per-row; markdown table ≤3× kept-whole | DEEPDIVE §1 L39, L50-51 |
| **Late Chunking** = paradigm shift mới | `late_chunking_enabled` **ON** (sliding/single) | PROJECT_ALL_FLOWS §6 U7 (L326) `ingest_stages_store.py:288-388` |
| Eval = context precision/recall + faithfulness | track **Faithfulness + Coverage** | CLAUDE.md "Coverage rate" |

→ **SỰ THẬT.** 6/6 mục research coi là "must-have" mình đã có sẵn và đang bật. Khung chunking đã expert-grade; vấn đề là *dây chưa nối hết* (block-native, narrate) chứ không phải khung sai.

---

## 4. Research chỉ GAP — ứng viên (lift CHƯA đo)

| Research khuyến nghị | Trạng thái code | Evidence |
|---|---|---|
| **Multimodal layout-aware** (Unstructured.io/Docling — phân loại Title/Para/Table/Image, không cắt ký tự thuần) | Parser Blocks tồn tại nhưng **chưa feed** vào `smart_chunk_atomic`; live path flatten về text | DEEPDIVE §0 + §1 L30, fix #6 (L164-168) |
| **Atomic-block protection** (TABLE/FORMULA/IMAGE never split) | HAVE nhưng **OFF default**, lại chạy trên text-regex không phải Block | DEEPDIVE §1 L35 (`FORMULA_IMAGE_ATOMIC_PROTECT_ENABLED=False`) |
| **Narrate-then-embed / per-table LLM description** (RAG-Anything Technique 1 — bảng có NL summary để embed) | **MISS / DORMANT** (`narrate_provider="null"`) | DEEPDIVE §1 L36, L41; fix #5 (L157-162) |
| **RFC-4180 CSV parse** (quoted multi-line cell không vỡ) | MISS — `text.split("\n")` làm vỡ cell đa-dòng | DEEPDIVE §2 case-A (L86-94), fix #1 (L134-138) |
| **Robust header detection** (không "first csv-shape line") | MISS — boilerplate bị chọn làm header | DEEPDIVE §2 (L96-104), fix #2 (L140-144) |
| **Column-aware mega-cell → dual-index→BM25** | MISS — row 5287-char giữ nguyên | DEEPDIVE §2 case-B (L113-125), fix #3 (L146-151) |
| **Boilerplate de-weight** | MISS — header lặp 100+ chunk | DEEPDIVE §1 L43, §2 (L106-108), fix #4 (L153-155) |

→ **GIẢ THUYẾT về lift.** Research cho benchmark (recursive-512 ≈ 69% acc; semantic vỡ vụn 54% / Doc-F1 0.42; late-chunking +10–12%) nhưng đó là **corpus học thuật của họ**, KHÔNG transfer sang corpus dịch vụ của mình. Phải load-test mới claim được %.

---

## 5. Map sâu BUG-1: Noise Problem → chuỗi gốc rễ cross-file

Research mô tả trừu tượng; code mình có chuỗi cụ thể (PROJECT_ALL_FLOWS §9-A):

```
factoid-giá trả sai entity (SỰ THẬT, load-test §0)
  ← LLM thấy chunk chứa NHIỀU dịch vụ co-occur            (Noise Problem — research §2)
  ← vector path retrieve chunk đa-entity
  ← query "giá <tên không-code>" KHÔNG route stats        (query_range_parser.py:374-377)
  ← chunk co-occur do table_dual_index GROUP chunk         (DEEPDIVE §6, csv_chunker dual_index 357-434)
  ← grounding judge warn-only KHÔNG chặn conflate          (PROJECT_ALL_FLOWS §5 L297)
```

**Immutable cause** (research-frame): catalog Q&A đi *fuzzy-vector* thay vì *structured-first* → vi phạm
nguyên tắc "1 entity = 1 vector atomic, không pha loãng". Fix đúng tầng (đã có trong DEEPDIVE §3):
table_csv **per-row exclusive bỏ group-chunk** (#3) + RFC-4180 parse (#1) + dedup stats rows (BUG-5).
Tầng routing (price-of-named-entity → stats) là fix song song ở §2, ngoài scope chunking.

---

## 6. VN-specific mandates (research Phần 7) ⟷ code

Research liệt kê "định luật kỹ thuật không thỏa hiệp" cho RAG tiếng Việt. Đối chiếu:

| Mandate | Code | Verdict |
|---|---|---|
| Word-segmentation trước embed | U6 `segment_vi_compounds` | ✅ có |
| Section-split theo kiến trúc pháp lý | HDT `_chunk_hdt` Chương/Mục/Điều | ✅ có |
| Micro-headers / metadata injection (bù cửa-sổ-hẹp) | `structural_path` breadcrumb prefix-vào-content | ✅ có (live path) ; nhưng `original_content` chưa persist trên live `smart_chunk` (DEEPDIVE §1 L37) ⚠️ |
| Overlap chunking sâu cho đoạn dài | `DEFAULT_CHUNK_OVERLAP` + late-chunking sliding | ✅ có |
| Giới hạn 256-tok của embedder VN (PhoBERT/bi-encoder) | embed spec lifted-from-spec, không hardcode dim; bkai_vn registry | ⚠️ embedder hiện jina/zeroentropy 1024-dim (PROJECT_ALL_FLOWS §6 U7 L326) — **mình KHÔNG bị trần 256** vì không dùng PhoBERT làm primary. Research-mandate này áp khi *chọn* embedder VN bản địa; hiện không áp. |

→ Phần lớn mandate VN mình đã tuân. Caveat 256-tok chỉ relevant nếu sau này swap sang embedder VN bản địa.

---

## 7. Eval framework (Evidently + research 4-trụ) ⟷ T1/T2 của mình

Research 4 trụ: Context Precision · Context Recall · Processing Efficiency · Resource Utilization.
Evidently: precision@k / recall@k + LLM-judge + synthetic-Q-from-chunk + faithfulness (reference-free).

| Trụ | Đo ở mình | Gap |
|---|---|---|
| Context Precision | — | chưa có metric riêng; CONFLATE = precision-fail dạng entity-map |
| Context Recall | **Coverage rate** (CLAUDE.md) | có khái niệm, cần harness đo đều |
| Faithfulness | grounding judge | ⚠️ **warn-only, không chặn** (PROJECT_ALL_FLOWS §5 L287, L297) → conflate/extrapolate lọt |
| Processing Efficiency | p95 latency | ❌ BUG-3 p95 ~15s |
| Resource | token/turn, cost | ⚠️ sysprompt ~2400 tok |

Evidently gợi ý **synthetic-Q-from-chunk** để build test-set — mình đã có `golden_set/` + `scripts/verify_fixes_loadtest.py` (22-case). → có hạ tầng, thiếu metric Context-Precision tách bạch.

---

## 8. Caveat trung thực (CLAUDE.md rule #0)

- **Mọi số benchmark trong research KHÔNG transfer** sang corpus của mình — chưa đo. Các % (69%/54%/+10-12%) là GIẢ THUYẾT cho tới khi load-test.
- Report này **chỉ map**, KHÔNG sửa code, KHÔNG claim "fix sẽ work".
- Evidence chunking lấy từ DEEPDIVE_CHUNKING_20260617 (đã verify file:line) — em **không** re-grep lại từng dòng trong phiên này; nếu cần độ chắc tuyệt đối từng `file:line`, phải re-verify trực tiếp `src/`.
- 3 URL (unstructured/viblo/studocu) chưa fetch — phần "multimodal/Docling" ở §4 dựa vào bản dán + Firecrawl, có thể bổ sung chi tiết nếu fetch.

---

## 9. Ứng viên next-step (report-only, quyết định để user)

Xếp theo impact T1 (đã có sẵn trong DEEPDIVE §3, research củng cố):

1. **[HIGH]** table_dual_index → **per-row exclusive, bỏ group-chunk** + RFC-4180 parse (DEEPDIVE #1, #3) — đánh thẳng Noise Problem/BUG-1.
2. **[HIGH]** Robust header detection + boilerplate de-weight (DEEPDIVE #2, #4).
3. **[MED]** Bật per-table LLM description = narrate-then-embed cho TABLE (DEEPDIVE #5 = RAG-Anything T1) — lever lớn cho NL/aggregation, cost-gated per-bot.
4. **[MED-eval]** Harness đo Context-Precision + Coverage per-strategy (Evidently synthetic-Q) để biến mọi claim chunking thành đo-được.
5. **[LOW]** Wire parser Blocks → `smart_chunk_atomic` (Wave B2) + persist `original_content` (DEEPDIVE #6).

> Mọi mục ở §9 cần `/plan` + user-approve trước khi đụng `src/` (Phase 4). Report này dừng ở READ+REPORT.

---
*Anchor: phiên 2026-06-18. Nguồn evidence: [DEEPDIVE_CHUNKING_20260617.md], [PROJECT_ALL_FLOWS_20260618.md], research tổng hợp + Firecrawl + EvidentlyAI.*
