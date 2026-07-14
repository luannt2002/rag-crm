> # ⚠️ ĐÃ BỊ THAY THẾ — KHÔNG DÙNG LÀM NGUỒN SỰ THẬT
> Nhiều claim trong file này đã bị bác ở tầng L5 (đọc code thật).
> Nguồn sự thật hiện tại: [reports/L5_CODE_TRUTH_20260714.md](L5_CODE_TRUTH_20260714.md)
> Giữ file này làm lịch sử điều tra, không phải kết luận.

---

# TRUTH VERIFICATION — 26 điểm "chưa expert" đối chiếu CODE THẬT + GIT + DB LIVE

**Ngày**: 2026-07-13
**Nhánh**: `fix-260623-ingest-expert` · **HEAD**: `71682a2`
**Mục đích**: verify lại từng cáo buộc trong `reports/EXPERT_AUDIT_MASTER_20260713.md` trước khi ship bất kỳ fix nào.
**Lý do phải verify**: audit trước đọc **hằng số trong code** rồi suy ra **hành vi runtime** — vi phạm rule #0. Report này thay bằng **bằng chứng runtime thật** (`request_steps`, `system_config`, `pg_stat_user_indexes`, `EXPLAIN`, `git blame`, `git log -S`).

**Trạng thái**: Phần B + C ĐÃ VERIFY XONG. Phần A / D1-D2 / E / F **ĐANG VERIFY** — sẽ append.

---

## 0. HAI QUY TẮC BẮT BUỘC RÚT RA TỪ ĐỢT VERIFY NÀY

### 0.1 — "Sửa `constants.py`" thường là **KHÔNG CÓ TÁC DỤNG**

Chuỗi resolve thật:

```
constants.py  →  PLAN_LIMIT_SCHEMA default  →  system_config (DB)  →  bots.plan_limits (per-bot)
                                                     ▲
                                          DB THẮNG nếu key tồn tại
```

**Nếu key có trong `system_config` → sửa `constants.py` = 0 tác dụng.** Mọi item trong plan **BẮT BUỘC** khai cột `CONSTANT hay DB?`.

Bằng chứng: `DEFAULT_MMR_SIMILARITY_THRESHOLD` **đã là 0.98** trong code từ 2026-07-04, nhưng `system_config` ghim **0.88** → runtime vẫn chạy 0.88 suốt 9 ngày.

### 0.2 — Git history CHỈ CÓ 26 NGÀY → "cũ" nghĩa là **MÙ**, không phải "an toàn"

```
cd08119   2026-06-17   first commit: ragbot RAG platform
362 commits — toàn bộ lịch sử
```

Repo bị **re-init** ngày 17/06. Project cũ hơn nhiều.

| Nhãn | Định nghĩa | Ý nghĩa cho fix |
|---|---|---|
| **KẾ THỪA** | `git blame` = `cd08119` | Code có **trước** git history. **KHÔNG commit message nào giải thích.** Git **KHÔNG THỂ** cho biết nó từng bị fix rồi regress hay chưa → **mình đang mù**. Bằng chứng duy nhất còn lại: comment / config DB / docs |
| **CÓ CHỦ ĐÍCH** | blame = commit sau `cd08119` | Người thật đụng vào trong 26 ngày qua, **có commit message nói rõ ý định** → đọc message là biết |

---

## 1. BẢNG TỔNG — 9 điểm đã verify

| # | Cáo buộc (audit cũ) | VERDICT | Fix bằng `constants.py`? | Nguồn gốc | Đã từng fix? |
|---|---|---|---|---|---|
| **B1** | Cliff floor bỏ qua `min_keep` → 18% query 1 chunk | ✅ **CONFIRMED** (18.1% khớp tuyệt đối) | ❌ **KHÔNG — bug THỨ TỰ** | KẾ THỪA | ⚠️ **CÓ — 3 lần** |
| **B2** | Seam `0.45 < 0.6` giết `recursive` | 🟡 **PARTIAL** — seam thật, 2 vế sai | ❌ KHÔNG — sửa contract selector | KẾ THỪA | ❌ chưa |
| **B3** | MMR chạy 0.88, repo đo 0.98 | ✅ **CONFIRMED runtime** · 🔴 **"sai constant" REFUTED** | ❌ **KHÔNG — cần ALEMBIC** | CÓ CHỦ ĐÍCH | ⚠️ **fix NỬA VỜI** |
| **B4** | `factoid` trong skip-list → 57.7% bỏ rerank | 🔴 **REFUTED — 0/741 = 0.0%** | ❌ **KHÔNG FIX GÌ CẢ** | KẾ THỪA | ❌ chưa |
| **B5** | `grade_timeout` 2.0 < p95 2.56 | ✅ CONFIRMED — **đã ship `5c4fdda`** | ✅ (đã xong) | CÓ CHỦ ĐÍCH | ✅ monotone |
| **C1** | VN tokenizer bất đối xứng (query thiếu segment) | 🔴 **REFUTED + ĐẢO CHIỀU 180°** | ❌ — fix ở **INGEST**, cần REINDEX | KẾ THỪA | ⚠️ **fix mắc kẹt chưa merge** |
| **C2** | NFC chỉ áp sparse, không áp dense | ✅ **CONFIRMED** | ❌ — sửa `_embed_query` | KẾ THỪA | ❌ chưa |
| **C3** | `vn_segment` gate bất đối xứng | ✅ CONFIRMED | ❌ — **XÓA** call query-side | KẾ THỪA | ⚠️ **fix ở `be94f58` CHƯA MERGE** |
| **D3** | Thiếu dim-guard per-vector | ✅ **CONFIRMED cả 3 vế** | ❌ — sửa ingest store + embedder | KẾ THỪA | ❌ chưa |
| **D4** | Coverage gate mù | ✅ CONFIRMED — 🔴 **nhưng SAI THỦ PHẠM** | ❌ — sửa strategy boundary | CÓ CHỦ ĐÍCH | ⚠️ **ship → mất → vớt lại** |

**Điểm số**: 2/9 **bị bác hoàn toàn** · 2/9 **sai bản chất** · 1/9 **sai thủ phạm**.
→ Ship thẳng audit cũ thì **5/9 là fix bẩn**.

---

## 2. B1 — CLIFF FILTER BỎ QUA `min_keep` ✅ CONFIRMED

### 2.1 Code thật

`src/ragbot/orchestration/retrieval_filter.py`:

```python
L127  floor_kept = [c for c in sorted_chunks if float(c.get("score", 0) or 0) >= absolute_floor]
                                                    # ↑ CẮT FLOOR TRƯỚC, không hỏi min_keep

L130  if not floor_kept and sorted_chunks and force_min_keep:
L131      return [sorted_chunks[0]], {... "reason": "empty_context_safety_keep_top1"}
                    # ↑ TRẢ ĐÚNG 1 CHUNK

L139  if len(floor_kept) <= 1:
L140      return floor_kept, {... "reason": "below_floor_or_single"}
                    # ↑ TRẢ 1 CHUNK — min_keep=3 BỊ PHỚT LỜ

L154  if gap > gap_ratio and i >= min_keep:
                                # ↑ min_keep CHỈ gác nhánh gap-cut
```

**`min_keep` KHÔNG phải sàn.** Nó chỉ gác **1 trong 3 lối ra**, và là lối **ít dùng nhất**.

### 2.2 Bằng chứng RUNTIME — `request_steps`, 741 row (2026-07-01 → 07-13)

```
n_kept = 1  →  134 row  =  18.1%          ← con số audit nói: KHỚP TUYỆT ĐỐI

cliff_reason:
  empty_context_safety_keep_top1  =  79   ← nhánh floor
  below_floor_or_single           =  55   ← nhánh floor      (79 + 55 = 134)
  no_cliff_kept_all               = 604
  cliff (gap-cut)                 =   3   ← 0.4% — nhánh DUY NHẤT min_keep bảo vệ
```

**`min_keep=3` gần như vô hiệu hoàn toàn.**

### 2.3 Chain resolve

`nodes/rerank.py:261-263` → `_pcfg` → `pipeline_config` → `resolve_bot_limit` → `system_config`.

| key | **LIVE DB** | constant |
|---|---|---|
| `rerank_filter_strategy` | `"cliff"` | `"cliff"` ✔ |
| `rerank_cliff_absolute_floor` | **0.2** | 0.2 ✔ |
| `rerank_cliff_min_keep` | **3** | 3 ✔ |
| `rerank_cliff_gap_ratio` | **0.5** | 0.35 ✗ **DRIFT — audit bỏ sót** |

Per-bot: 1 bot override `rerank_cliff_min_keep: 5` qua `plan_limits`.
→ **Key có trong DB ⇒ sửa `constants.py` là chết.**

### 2.4 ⚠️ VÒNG FIX-REFIX — CẤM TUNE LẠI SỐ

```
0.15  (alembic 0068, 2026-05-08)
  ↓   gây REFUSE_GAP — có load-test chứng minh
0.05  (S2 stream, 2026-05-11)  — evidence: reports/LOADTEST_90Q_RESULT_20260511_161747.json
  ↓
0.2   (c0c0dea "audit-driven best-practice fixes")   ← HIỆN TẠI
```

`tests/unit/test_cliff_floor_calibrated.py` **vẫn đang canh** window `[0.0, 0.20]`, cảnh báo floor > 0.20 sẽ **tái hiện regression REFUSE_GAP thời 0.15**.
**0.2 hiện tại nằm ĐÚNG TRÊN TRẦN.**

### 2.5 Ý định tác giả — code làm NGƯỢC lại chính nó

`shared/constants/_01_...py:164-169` (comment nguyên văn):

> *"Default 3 (không phải 1): một lần reranker chấm sai KHÔNG được làm sập tập chunk còn lại xuống một. Forensic step-level (2026-06-05, tra cứu điều khoản pháp lý)... với min_keep=1 cliff sẽ drop nó, nên LLM không bao giờ thấy đáp án."*

**Tác giả viết đúng ý định. Code không thực hiện được ý định đó.**

### 2.6 FIX ĐÚNG

**Đổi THỨ TỰ, KHÔNG đổi số.** Sau khi cắt floor, nếu `len(floor_kept) < min_keep` → **back-fill** từ `sorted_chunks` cho đủ `min_keep`.

→ **Pattern này ĐÃ CÓ SẴN** trong `mmr_filter` (`DEFAULT_MMR_MIN_KEEP`, ship ở 002-D). **TÁI DÙNG, KHÔNG PHÁT MINH.**

- **KHÔNG** hạ `absolute_floor` — đó là số đã tune 3 lần và có regression guard đang sống.
- Test cần thêm: `len(out) >= min_keep` khi đầu vào đủ. **Đây là invariant MỚI hợp lệ**, không phải gaming.
- **Blast radius**: `_cliff_detect_filter` có **1 caller** (`nodes/rerank.py:277`). Test ghim: `test_cliff_detect_filter.py`, `test_cliff_floor_calibrated.py`, `test_rerank_defaults_recalibrated.py`, `test_rerank_safety_net_score_preservation.py`, `test_reranker_threshold_gate.py`, `test_pipeline_config_per_bot_resolve.py`.

---

## 3. B2 — SEAM CHUNKING `0.45 < 0.6` 🟡 PARTIAL

### 3.1 ✅ Seam CÓ THẬT

`src/ragbot/shared/chunking/analyze.py`:

```python
L538  if confidence < DEFAULT_STRATEGY_MIN_CONFIDENCE:          # 0.45
L539      return (CHUNK_STRATEGY_RECURSIVE, DEFAULT_STRATEGY_MIN_CONFIDENCE)
                                             # ↑ trả về ĐÚNG 0.45

L661  if confidence < conf_threshold:                            # 0.6  (L5 rule 1)
L662      overrides.append((CHUNK_STRATEGY_HYBRID, ..., "low_confidence_fallback"))
```

`0.45 < 0.6` **luôn đúng** → nhánh **fallback** recursive phát ra một confidence mà **stage ngay sau chắc chắn từ chối**. Mọi strategy thắng trong dải `[0.45, 0.6)` cũng bị viết đè thành `hybrid`.

Flag L5 **ĐANG BẬT** ở production: `system_config.adapchunk_layer5_cross_check_enabled = true`.

### 3.2 🔴 BÁC 2 vế của audit cũ

| Audit cũ nói | Sự thật |
|---|---|
| "`recursive` unreachable" | ❌ **SAI** — recursive vẫn **thắng bằng max-score** khi conf ≥ 0.6. `test_adapchunk_l5_crosscheck.py:127-128` chứng minh `apply_cross_check("recursive", 0.85, ...)` sống sót nguyên vẹn |
| "default thật = `hybrid→proposition`" | ❌ **SAI** — `apply_cross_check` trả `overrides[0]` (match đầu tiên), rule 1 append **trước** → rơi vào **`hybrid` rồi DỪNG**. `proposition` chỉ tới được qua rule 3, mà rule 3 cần conf ≥ 0.6 để sống qua rule 1 |

**Xác nhận bằng DB**: strategy live trong corpus là `recursive` (**689 chunk**) và `hdt` (**217 chunk**). **`proposition` KHÔNG LIVE.**

### 3.3 Gốc rễ thật: 2 con số từ 2 nguồn, **chưa bao giờ đối chiếu**

- `0.6` — **CÓ NGUỒN**, ghi trong comment `analyze.py:557-559`: *"Databricks AI-Driven Chunking blog (2024): 'simple fallback to hybrid when confidence < 0.6'"*
- `0.45` — **KHÔNG có bất kỳ lời giải thích nào**. Không comment, không ADR, không plan, không report.

### 3.4 Chain resolve

- `DEFAULT_STRATEGY_MIN_CONFIDENCE` → đọc **TRỰC TIẾP từ constant** (`analyze.py:538`). Không `_pcfg`, không DB. → **sửa `constants.py` CÓ tác dụng** (hiếm).
- `adapchunk_l5_confidence_threshold` → `get_boot_config(...)`, **không có trong `system_config`** → constant `0.6` là live.

### 3.5 FIX ĐÚNG

Đây là **bug KHÔNG NHẤT QUÁN giữa 2 ngưỡng, không phải 1 con số sai.** Fallback không được phát ra confidence mà guard ngay sau nó chắc chắn từ chối.

Sửa ở **`analyze.py` (contract của selector)**, KHÔNG ở `constants.py`:
- (a) trả về **confidence THẬT** (`scores[best]`) kèm `recursive` để L5 xét tín hiệu thật, HOẶC
- (b) rule 1 **bỏ qua** input đã được đánh dấu là fallback-cố-ý.

**KHÔNG** nhích `0.45` → `0.6` — chỉ che bug (fallback sẽ nằm đúng biên, `<` vẫn loại 1 phía).

### 3.6 ⚠️ CHƯA VERIFY — chặn ship

**Không đo được seam bắn bao nhiêu lần.** Strategy **chỉ log structlog**, không ghi DB:
- `document_chunks.metadata_json` — 902 row null strategy
- `audit_log` — **0 event** `adapchunk%`

→ **PHẢI THÊM 1 VIỆC TRƯỚC**: persist `chunk_strategy` vào metadata. **CẤM claim tác động khi chưa đo.**

---

## 4. B3 — MMR 0.88 vs 0.98 ✅ CONFIRMED runtime · 🔴 "SAI CONSTANT" REFUTED

> **ĐÂY LÀ CÁI BẪY ĐẮT NHẤT TOÀN AUDIT.** Sửa `constants.py` = **0 tác dụng**.

### 4.1 Constant ĐÃ ĐÚNG RỒI

```python
_14_...py:235   DEFAULT_MMR_SIMILARITY_THRESHOLD: Final[float] = 0.98   ← ĐÃ FIX (9f93804, 2026-07-04)
_14_...py:258   DEFAULT_MMR_SIMILARITY_THRESHOLD_BY_INTENT = {"factoid": 0.88, ...}  ← VẪN 0.88, VÀ MAP NÀY THẮNG
```

`nodes/mmr_dedup.py:35-48` — **map per-intent được hỏi TRƯỚC**; global chỉ là nhánh `else`.

### 4.2 DB GHIM NGƯỢC — bằng alembic CÓ CHỦ ĐÍCH, ĐÃ APPLY

```
system_config.mmr_similarity_threshold           = 0.88     (constant nói 0.98)  ← DB THẮNG
system_config.mmr_similarity_threshold_by_intent = {"factoid":0.88, "comparison":0.95,
                                                     "multi_hop":0.95, "aggregation":0.98}
system_config.mmr_min_keep                       = (vắng) → constant 3 live ✔
```

`alembic/versions/20260709_seed_cliff_floor_mmr_parity.py` — **docstring nguyên văn**:

> *"`mmr_similarity_threshold` — constant là 0.98, production DB là 0.88. … việc nâng lên 0.98 là **một quyết định đo-lường RIÊNG** (MMR flip). Migration này **CHỈ ghim giá trị production hiện tại (0.88)** để clone mới khớp live."*

Kiểm tra ancestry: migration **ĐÃ NẰM TRONG CHAIN ĐANG APPLY**.
→ Divergence này **được biết, có chủ đích, có document**. Tác giả **cố ý hoãn flip**.

### 4.3 Runtime thật — `request_steps` (`mmr_dedup`, 741 row)

| intent | before | after | threshold live | n |
|---|---|---|---|---|
| `factoid` | 4.77 | **3.19** | **0.880** | 604 |
| `comparison` | 9.30 | 6.64 | 0.950 | 44 |
| `aggregation` | 15.92 | 14.29 | 0.980 | 38 |

**factoid: −33%** (audit nói −29%, gần đúng nhưng số thật nặng hơn).

### 4.4 Fix NỬA VỜI — không phải revert loop

`9f93804` (2026-07-04) `fix(mmr): survivor floor min_keep + measured threshold recalibration (002-D)` — **commit body**:

> *"ĐO TRƯỚC (theo plan): trên zembed-1, cosine giữa các section KHÁC NHAU CÙNG 1 DOC (p50 0.975, max 0.990) **chồng gần hoàn toàn** lên dải near-duplicate — **KHÔNG ngưỡng nào tách được**; 0.88 cũ (calibrate thời TRƯỚC khi swap embedder) **dedup nhầm 100% cặp section phân biệt**, làm sập doc có section 6→1 và bỏ đói generate → bịa."*

→ **Số 0.98 ĐÃ ĐƯỢC ĐO. KHÔNG cần đo lại.** Evidence: `specs/002-deepdebug-luannt/evidence/debug_findings.json`.
Người trước: **đo xong → sửa constant → QUÊN flip DB → quên map per-intent.**

### 4.5 🔴 1 TEST ĐANG ĐỎ TẠI HEAD (audit bỏ sót)

```
tests/unit/orchestration/test_per_intent_caps.py::test_default_constant_aggregation_loosens_threshold
E   AssertionError: aggregation must get a LOOSER MMR threshold than the default
E   assert 0.98 > 0.98
1 failed, 33 passed
```

Vỡ do chính `9f93804` fix nửa vời (nâng global lên 0.98 nhưng để `aggregation` ở 0.98 → invariant `>` sập).

### 4.6 FIX ĐÚNG

**ALEMBIC MỚI**, không phải sửa constant. **PHẢI update CẢ HAI**:
1. `system_config.mmr_similarity_threshold` : 0.88 → 0.98
2. `system_config.mmr_similarity_threshold_by_intent.factoid` : 0.88 → 0.98 ← **thiếu cái này thì update (1) VÔ NGHĨA** (map per-intent thắng ở `mmr_dedup.py:37`)

Rồi sync `DEFAULT_MMR_SIMILARITY_THRESHOLD_BY_INTENT` trong `_14`.
`test_per_intent_caps.py:242` đổi `aggregation > global` → `aggregation >= global` — **HỢP LỆ**: invariant cũ là workaround cho phân phối embedding TRƯỚC khi swap; global đã recalibrate đúng thì việc nới per-intent là **thừa theo cấu trúc**, không phải gaming.
**KHÔNG đụng `DEFAULT_MMR_MIN_KEEP=3`** — đã live và đang bảo vệ sàn.

---

## 5. B4 — `factoid` TRONG RERANK SKIP-LIST 🔴 **REFUTED — GẠCH KHỎI PLAN**

### 5.1 Audit cũ đọc hằng số rồi SUY RA runtime — vi phạm rule #0

Hằng số **đúng là có** `factoid`:
```python
_02_...py:13   DEFAULT_RERANK_SKIP_INTENTS = frozenset(
                   {"chitchat", "oos", "greeting", "feedback", "vu_vo", "factoid"})
```

**Nhưng audit BỎ SÓT vế thứ hai của điều kiện AND** — `nodes/rerank.py:140-145`:

```python
_size_safety = len(inp) <= int(top_n)
_intent_skip_set = (
    bool(_skip_set)
    and _intent_lc in _skip_set
    and _size_safety          # ←←← AUDIT BỎ QUA DÒNG NÀY
)
```

Query `factoid` **chỉ** skip rerank khi **pool ứng viên đã lọt vào trong `top_n`** — tức là **không còn gì để xếp hạng**. Thiết kế **ĐÚNG**, có document ở `_02:6-8`.

### 5.2 Bằng chứng RUNTIME — `request_steps` (`step_name='rerank'`, 741 row)

```
mode                count    pct
rerank               740    99.9%
rerank_fallback        1     0.1%
intent_skip_set        0     0.0%     ←←← BẰNG KHÔNG. KHÔNG PHẢI 57.7%.
```

**Vì sao không bao giờ bắn:**
```
input <= top_n ?     false: 700 (94.5%)     true: 41 (5.5%)
pool/top_n phân bố:  20/7 → 566 row · 20/5 → 55 · 20/20 → 33 · 20/12 → 25
```
Retrieval đưa pool **20**, `factoid` có `top_n=7` → `20 <= 7` = **false** → **KHÔNG BAO GIỜ SKIP**.
41 row có size-safety pass đều là `20/20` = **`aggregation`**, mà `aggregation` **không nằm trong skip set**.

### 5.3 KẾT LUẬN: **KHÔNG FIX GÌ CẢ**

Gỡ `factoid` khỏi skip set sẽ:
- **no-op trên traffic thật** (0% request tới nhánh đó)
- **vỡ ~10 assertion** ở 3 file test (`test_per_intent_rerank_skip.py` có 18 assertion, `test_request_step_top_score_metadata.py`, `test_chat_worker_config_batch.py`)

→ **Đây CHÍNH XÁC là "fix bẩn làm nát code".** GẠCH KHỎI PLAN.

**Việc DUY NHẤT đáng làm**: audit sai vì đọc constant thay vì runtime → **thiếu observability**. `mode=intent_skip_set` không lên dashboard nào.

---

## 6. B5 — `grade_timeout` ✅ ĐÃ XONG

- `_10_rbac.py:180` — `DEFAULT_GRADE_TIMEOUT_S: Final[float] = 3.0`
- Chain: `_pcfg` → **`grade_timeout_s` KHÔNG có trong `system_config`, không bot nào set `plan_limits`** → **LIVE = 3.0 thẳng từ constant.** Đây là item **DUY NHẤT** mà sửa constant chính là toàn bộ fix — và đã sửa rồi.
- Grep toàn `src/ tests/ alembic/`: **0 chỗ hardcode 2.0**.
- Justify tại chỗ (`_10:174-179`): *"nằm ngay TRÊN p95 đo được (2.56s) để một lần grade nguội hoàn tất thay vì bị ép pass ungraded; 2.0s cũ hụt ngay dưới p95 của chính nó."*
- `test_grade_timeout_cap.py` ghim `>= p95` và `<= 3.5` → 3.0 trong window, **trần đã có guard**.

**ĐÓNG ITEM.**

---

## 7. C1 — VN TOKENIZER 🔴 **REFUTED + ĐẢO CHIỀU 180°**

> **NẾU SHIP THEO AUDIT CŨ → RETRIEVAL TỆ ĐI.**

### 7.1 Tiền đề của audit là SAI: index **KHÔNG HỀ** segmented

Postgres coi `_` là **`blank` token = dấu phân cách** → nó **XÓA** underscore. Bằng chứng DB live:

```sql
ts_debug('simple','chăm_sóc')            →  word 'chăm' | blank '_' | word 'sóc'
to_tsvector('simple','chăm_sóc da mặt')  →  'chăm':1 'da':3 'mặt':4 'sóc':2
```

Ingest ghi `content_segmented` = `CHĂM_SÓC`, nhưng trigger
`update_chunk_search_vector() = to_tsvector('simple', COALESCE(NEW.content_segmented, NEW.content, ''))`
**bẻ nó về lại 2 unigram**. Mẫu từ `search_vector` của 1 row thật: `'chăm':32 'sóc':33` — **tách rời, KHÔNG có lexeme ghép**.

Quét toàn corpus tìm lexeme có underscore → **chỉ có URL**. **ZERO lexeme từ ghép VN tồn tại trong index.**

### 7.2 Query hiện tại **ĐANG ĐÚNG**

`pg_bm25_retrieval.py:113,119` → `websearch_to_tsquery('simple', :query)` → `'chăm' & 'sóc'` → **KHỚP index**. Live: **20 hit**, không phải 0.

**Nếu segment query như audit đề xuất:**
`websearch_to_tsquery('simple','chăm_sóc')` → `'chăm' <-> 'sóc'` = **phrase-adjacency**, **HẸP HƠN** `&` → **GIẢM recall**.

### 7.3 🔥 BUG THẬT — ở chiều NGƯỢC LẠI, ĐANG CHẢY MÁU RECALL

Segmentation ở **INGEST** đang **PHÁ HỦY token**. Đo trên corpus live (brand token ẩn danh = `<brand-token>`):

| | |
|---|---|
| Chunk chứa `<brand-token>` | **28** |
| Hit với index **hiện tại (segmented)** | **4** |
| Hit nếu **KHÔNG segment** | **28** |

→ **24/28 chunk BẤT KHẢ TRUY CẬP** cho query tên thương hiệu.

Cơ chế: underthesea nối thành `<Prefix>._<Brand>` → parser nuốt thành **1 token kiểu `file`** thay vì 2 word.

Toàn corpus: **436/906 chunk** có `to_tsvector(content) <> to_tsvector(content_segmented)`. Token **bị mất** đúng là loại đắt nhất: tên thương hiệu, và các token đơn-giá dạng `/45`, `/55`, `/65`.

**Net: segmentation cho recall = 0 lợi ích (underscore bị xóa) + hại ĐO ĐƯỢC.**

### 7.4 Nguồn gốc + comment NÓI DỐI

- **KẾ THỪA** — `git blame` toàn bộ `pg_bm25_retrieval.py:105-125` và `pgvector_store.py:409` = `cd08119`. **Chưa từng đụng lại.** Git **không thể** cho biết từng fix chưa.
- Ship có chủ đích: `STATE_SNAPSHOT_HISTORY.md:1936` — *"P22 Option B VN compound segmentation tại ingest | plan 260423-P22 | migration 0046 `content_segmented` column + tsvector trigger, +11 unit"*
- **Comment `pgvector_store.py:406-408` SAI SỰ THẬT**: *"ingest indexes content_segmented (compound joined via `_`); query side must mirror that…"* → **parser xóa `_`, không có gì để mirror.**
- **`tests/unit/test_bm25_symmetric_segment.py` ĐANG GHIM HÀNH VI SAI** — nó assert query phải segment giống ingest. **Test này bảo vệ bug.**

### 7.5 ⚠️ FIX ĐÃ CÓ, MẮC KẸT CHƯA MERGE

`git log --all -S'segment_vi_compounds'` → commit **`be94f58` "expert remediation (Wave2)"** thêm language gate cho query segmenter + test `test_pgvector_segment_language_gate.py`.

`git merge-base --is-ancestor be94f58 HEAD` → **KHÔNG PHẢI ANCESTOR — CHƯA MERGE.** Sống trên `integ-260624-wave1`.

→ **Có người đã chẩn đoán đúng (một phần) rồi bỏ đó.**

### 7.6 FIX ĐÚNG — **NGƯỢC 180° so với audit**

**Gỡ segmentation khỏi luồng tsvector Ở INGEST:**
1. Trigger index `NEW.content` thay vì `COALESCE(NEW.content_segmented, NEW.content)`
2. Xóa 2 call `segment_vi_compounds` ở query-side (`pgvector_store.py:409,417`) — chúng chỉ làm hẹp `&` → `<->`
3. **Nghỉ hưu** test `test_bm25_symmetric_segment.py` (nó ghim bug)

⚠️ **CẦN REINDEX** — tsvector là trigger-materialised → phải rebuild `search_vector` **toàn bộ corpus**. **KHÔNG cần re-ingest** (source text không đổi).
`content_segmented` để nguyên (vô hại) hoặc drop ở đợt sau.

**KHÔNG merge gate của `be94f58`** — gate cho một call sắp bị xóa là vô nghĩa.

> *(Nếu sau này thật sự muốn match theo từ ghép: cơ chế đúng là **VN text-search dictionary/config thật**, không phải nối underscore — parser `simple` VĨNH VIỄN không coi `_` là ký tự từ.)*

---

## 8. C2 — NFC NORMALIZE ✅ CONFIRMED

| Luồng | Có normalize? | Bằng chứng |
|---|---|---|
| **Ingest** | ✅ | `document_service/text_processing.py:82` → `text = normalize_vn(text)` |
| **Sparse query** | ✅ | `pgvector_store.py:391` → `query_text = normalize_vn(query_text)` — comment: *"NFC normalize to match ingest path; NFD inputs (macOS/mobile) would otherwise miss NFC-indexed content."* |
| **Dense query (embedding)** | 🔴 **KHÔNG** | `_embed_query` (`query_graph.py:1553`) đưa `query_text` **thẳng** vào `embedder.embed_one(...)` (:1638). **Không có `normalize_vn` ở đâu trong hàm.** Và `grep normalize_vn\|unicodedata src/ragbot/infrastructure/embedding/*.py` → **0 hit** |

→ Query từ **macOS/iOS (NFD)** được **embed ở dạng NFD**, trong khi corpus embed ở **NFC** → **lệch không gian vector**. Dòng `:391` "sửa" nó chỉ cho nhánh sparse, **SAU KHI vector đã sinh xong**.

- **Nguồn gốc**: **KẾ THỪA** (`cd08119`). File đụng 6 lần nhưng **dòng 391 chưa từng đụng lại**.
- **Không cố ý** — comment ở `:389-390` nói rõ ý định là **đối xứng với ingest**; thiếu ở dense là **sơ suất**, không có ADR/plan nào bênh.
- **FIX ĐÚNG — query side**: gọi `normalize_vn(query_text)` **1 lần ở đầu `_embed_query`**, **TRƯỚC** cache lookup (`:1606`) → cả cache key lẫn text lên wire đều canonical.
  **KHÔNG** nhét vào embedder adapter — đẩy mối bận tâm VN-specific vào provider strategy là **vi phạm domain-neutral**.
- **Blast radius: THẤP.** NFC idempotent → no-op với traffic NFC đang chạy tốt; chỉ đổi hành vi cho input NFD đang âm thầm hỏng. **Không cần reindex** (corpus đã NFC). Lưu ý: cache embedding key theo `query_text` → normalize trước cache sẽ **gộp key NFC/NFD** (đổi cache key, không phải rủi ro correctness).

---

## 9. C3 — `vn_segment` GATE BẤT ĐỐI XỨNG ✅ CONFIRMED

```python
# INGEST — gate 2 điều kiện
ingest_stages.py:996    elif vi_seg_enabled and _vi_seg_lang_eligible:

# QUERY — KHÔNG GATE GÌ CẢ
pgvector_store.py:409   tokenized_query      = segment_vi_compounds(query_text)        # vô điều kiện
pgvector_store.py:417   tokenized_normalized = segment_vi_compounds(normalized_query)  # vô điều kiện
```
`grep "seg_eligible\|lang_eligible\|VI_DOMAIN_LANGUAGES" pgvector_store.py` → **0 hit ở HEAD.**
→ Bot **thuần tiếng Anh** vẫn bị segment query dù corpus của nó **chưa bao giờ** được segment.

Đường thứ 3: `PgBM25Retrieval` **không segment, không gate** — **hành vi thứ ba, khác cả hai.**

- `system_config.vi_compound_segmentation_ingest_enabled = true` → gate ingest **đang mở**.
- **Nguồn gốc**: KẾ THỪA (`cd08119`).
- ⚠️ **Fix đã có ở `be94f58` — CHƯA MERGE** (xem §7.5).
- **FIX ĐÚNG**: **KHÔNG merge gate.** Theo C1 → **XÓA** cả 2 call query-side. **Một call đã bị xóa thì không cần gate.** Giữ gate ingest cho tới khi gỡ luôn segmentation ở ingest. Không cần reindex cho riêng thay đổi này.

---

## 10. D3 — THIẾU DIM-GUARD ✅ CONFIRMED CẢ 3 VẾ

### 10.1 COUNT guard **CÓ** — đúng, fail loud

`ingest_stages_store.py:521`:
```python
if len(embed_results) != len(_chunks_needing_embed):
    logger.error("embedding_length_mismatch_aborting_ingest", ...)
    # soft-delete doc → state='failed'
    raise ExternalServiceError(...)
```

### 10.2 DIM guard per-vector **KHÔNG CÓ**

Chỗ duy nhất có `len(vector)` trong store path (`:555-560`):
```python
_dim = 0
if embed_results and embed_results[0] is not None:      # ← CHỈ ĐỌC PHẦN TỬ [0]
    try:
        _dim = len(embed_results[0])
    except TypeError:                                    # ← NUỐT LỖI
        _dim = 0
_audit.log(... {"dim": _dim} ...)                        # ← CHỈ LÀ METADATA AUDIT, KHÔNG GATE GÌ
```
Vector `[1:]` **không bao giờ được kiểm dim**. Vector ngắn/lệch sẽ tới thẳng `INSERT` → hoặc raise `DataException` mờ mịt lúc ghi, hoặc **nằm sai cột dim**.

**Dim check THẬT có tồn tại — nhưng CHỈ trong `health_check`** (`zeroentropy_embedder.py:163`):
```python
if len(vec) != self._dimensions:
    logger.warning("embedder_health_check_dim_mismatch", ...)
    return False
```
Comment `:158-161` cho thấy **tác giả BIẾT** — *"provider đổi default sẽ âm thầm phá ingest với `DataException` chỉ lộ ra lúc insert (thảm họa, mất cả batch). Bắt ở warmup."*
→ Chọn gác **warmup THAY VÌ** hot path. **Provider flip giữa chừng KHÔNG được bảo vệ.**

### 10.3 Wire dim **HARDCODE**, `spec.dimension` KHÔNG BAO GIỜ ĐƯỢC ĐỌC

```python
zeroentropy_embedder.py:77    dimensions: int = DEFAULT_ZEROENTROPY_EMBEDDING_DIM,   # 1280 — ctor default
zeroentropy_embedder.py:229   "dimensions": self._dimensions,                        # ← gửi giá trị CTOR lên wire
```
`grep "spec.dimension" zeroentropy_embedder.py` → **không có trong request payload.**

**Và tồn tại 2 default XUNG KHẮC:**
```
DEFAULT_EMBEDDING_DIM             = 1024   (_00_app_env_taxonomy.py:146)
DEFAULT_ZEROENTROPY_EMBEDDING_DIM = 1280   (_02_...py:68)
DB column                         = vector(1280)
```
→ Bot có `ai_models.dimension ≠ 1280` **vẫn bị embed ở 1280**. Bug đang **tiềm ẩn** chỉ vì cột DB tình cờ khớp ctor default.

### 10.4 FIX ĐÚNG — 2 thay đổi, phía ingest

1. **`ingest_stages_store.py`**: ngay sau count-guard `:521`, thêm **dim check per-vector** đối chiếu `spec.dimension` — fail-loud, **dùng lại đúng đường hồi phục soft-delete + `ExternalServiceError`** mà count-guard đã có. Đây là **nhà đúng** của invariant này (nó đã sở hữu "không bao giờ âm thầm lưu embedding NULL").
2. **`zeroentropy_embedder.py`**: lấy `dimensions` trên wire **từ `spec.dimension`** (fallback ctor khi `spec is None`) → model spec là single source of truth.

⚠️ **CHẶN**: phải **audit `ai_models.dimension` TRƯỚC** khi đổi nguồn wire dim — nếu có row nào khai dim ≠ 1280, ingest sẽ bắt đầu ghi vector mà cột `vector(1280)` từ chối.

- **Blast radius (chỉ riêng guard)**: **an toàn, thuần bổ sung** — hôm nay mọi vector đều 1280 (cột DB cũng ép vậy) → corpus đúng thì **0 failure mới**; chỉ biến `DataException` mờ mịt tương lai thành abort fail-loud sạch sẽ. **Không cần reindex.**
- **Nguồn gốc**: KẾ THỪA (`cd08119`) cả 2 dòng. Không có vòng fix-refix — đây là **khoảng trống chưa đóng**, không phải bug tái phát.

---

## 11. D4 — COVERAGE GATE MÙ ✅ CONFIRMED · 🔴 **NHƯNG SAI THỦ PHẠM**

### 11.1 Cơ chế mù

Gate định vị chunk bằng **tìm chuỗi con chính xác** — `shared/chunking/coverage.py:203`:
```python
pos = norm_source.find(norm_chunk, cursor)
if pos == -1:
    pos = norm_source.find(norm_chunk)   # thử lại từ đầu
if pos == -1:
    unlocated += 1
    continue                              # → KHÔNG đóng góp interval nào
```
Chunk nào mang text **không có trong source** → **không định vị được** → cả đoạn đọc thành gap.

### 11.2 🔴 Thủ phạm KHÔNG PHẢI `proposition`

Audit cũ đổ cho `proposition`. **`proposition` KHÔNG LIVE.** Corpus thật:
```sql
SELECT metadata_json->>'chunking_strategy', count(*) FROM document_chunks GROUP BY 1;
  recursive → 689
  hdt       → 217
```

Thủ phạm thật: **`_chunk_hdt` (217 chunk LIVE)** prepend path prefix **lúc CHUNK** (trước khi gate chạy) — `shared/chunking/strategies.py`:
```python
:351   prefix = f"[{path_info['full']}]\n"     # trong _chunk_hdt
:754   prefix = f"[{path_info['full']}]\n"     # trong _chunk_hybrid
:767   prefix = f"[{path_info['full']}]\n"     # trong _chunk_hybrid
```
(`_chunk_proposition` ở `:631` **tự nó không mutate** — chỉ split; prefix do **caller `_chunk_hybrid`** thêm ở `:754`.)

### 11.3 Repro xác định (agent chạy `check_chunk_gaps` thật, tách đúng 1 biến)

| hình dạng chunk | `ok` | `coverage_ratio` | `unlocated` |
|---|---|---|---|
| có path-prefix (`hdt`/`hybrid`) | False | **0.0000** | 1 |
| verbatim (`recursive`) | False | 0.8462 | 0 |

**0.0000 trong khi KHÔNG mất gì cả.** Gate **không phân biệt được** "mất sạch dữ liệu" với "chunker thêm 1 dòng header".

### 11.4 Gate còn **KHÔNG RĂNG** theo thiết kế

`ingest_stages.py:890` → `_cov = check_chunk_gaps(...)`, chỉ `if not _cov.ok: logger.warning(...)`.
Comment `:886-888` nói thẳng: *"NEVER raises — pure observability, so it can only LOG."*
→ **Ngay cả 0.000 THẬT cũng không chặn gì.**

### 11.5 ⚠️ VÒNG SHIP–MẤT–VỚT LẠI

```
75f5c96  feat(chunking): P0-2 FULL lossless char-coverage gate (OBSERVE-only superset)
   ↓ (mất / revert)
d7bd5ac  feat(chunking): P0-2 full char-coverage gate module (SALVAGED FROM WAVE-1, constants to SSoT)
```
**Ship → mất → vớt lại.** Nguồn gốc dòng wiring: **CÓ CHỦ ĐÍCH** — `bd409907` (2026-06-29).

⚠️ **CẤM tune `DEFAULT_COVERAGE_TOL` (0.02)** — ratio **không phải sai hiệu chỉnh**, nó **vô nghĩa về mặt cấu trúc** với strategy có prefix. Không con số `tol` nào sửa được `find() == -1`.

### 11.6 FIX ĐÚNG — ở **strategy boundary**

Strategy **không được đưa cho gate cái text mà chính nó bịa ra**.

- **Tốt nhất**: `_chunk_hdt` / `_chunk_hybrid` trả path prefix **qua metadata**, không nối vào chuỗi chunk; persist step gắn lại → `check_chunk_gaps` so text-từ-source với source.
- **Fallback (kém hơn)**: strip prefix `[path]\n` bên trong `check_chunk_gaps` trước khi `find()` — **nhưng nó ghép gate vào định dạng của 1 strategy**, đây là fix kém.
- **KHÔNG** "fix" bằng cách nới `tol`.

- **Blast radius**: **an toàn** — gate là OBSERVE-only nên ratio đúng lên **không đổi outcome ingest nào**, chỉ đổi nội dung log/metric. **Nhưng nó MỞ ĐƯỜNG** cho việc biến gate thành assert cưỡng chế (mục tiêu đã tuyên bố trong skill `block-integrity-quality-gate`) — và **việc đó blast radius CAO**: gate cưỡng chế trên corpus đang báo 0.000 sẽ **từ chối gần như mọi doc `hdt`**.
  → **SỬA LOCATOR TRƯỚC. Chỉ sau đó mới bàn tới cưỡng chế.** Không cần reindex.

### 11.7 ⚠️ CHƯA VERIFY (agent tự khai)

`request_steps` chỉ có **đúng 1 row** `ingest_chunk`, metadata **không có** `char_coverage_ratio` (nghĩa là `_cov.ok = True` cho doc đó). **n=1 không đủ** kết luận tần suất mis-fire ở production.
→ **Cần**: ingest 1 doc route qua `hdt` với step-tracking bật, đọc lại `char_coverage_ratio`.

> **Agent tự bác bằng chứng của chính nó** (đúng chuẩn rule#0): lần đo đầu trên 6 doc thật cho `cov=0.0000, unlocated=100%` nhưng nó dùng `documents.raw_content` làm source và `document_chunks.content` **post-enrich** làm chunk — mà enricher prepend `f"Tài liệu: {title}. Đoạn {position}."` (`shared/contextual_enrichment.py:75`), **không phải cái gate thấy ở U4**. Agent tuyên bố: *"lần chạy đó PHÓNG ĐẠI vấn đề và tôi KHÔNG dựa vào nó"* → dùng repro tổng hợp sạch ở §11.3.

---

## 12. NHỮNG THỨ AUDIT CŨ BỎ SÓT HOÀN TOÀN

| # | Phát hiện | Bằng chứng |
|---|---|---|
| **X1** | **`rerank_cliff_gap_ratio` DRIFT** | `system_config` = **0.5** · constant = **0.35**. **DB thắng.** Không document ở đâu |
| **X2** | **1 TEST ĐANG ĐỎ TẠI HEAD** | `test_per_intent_caps.py::test_default_constant_aggregation_loosens_threshold` → `assert 0.98 > 0.98` **FAIL**. Vỡ do `9f93804` fix nửa vời |
| **X3** | **Docstring nói dối** | `analyze.py:567` ghi flag L5 *"default OFF"* — thực tế **ON** cả ở constant (`_12:149`) lẫn `system_config` |
| **X4** | **Comment nói dối** | `pgvector_store.py:406-408` bảo "ingest indexes content_segmented, query phải mirror" — **parser xóa `_`, không có gì để mirror** |
| **X5** | **`be94f58` "expert remediation (Wave2)" CHƯA MERGE** | Có fix + test cho C3, mắc kẹt trên `integ-260624-wave1` |
| **X6** | **`test_bm25_symmetric_segment.py` GHIM BUG** | Nó assert hành vi SAI (query phải segment giống ingest) → test **bảo vệ** bug |
| **X7** | **Strategy KHÔNG persist vào DB** | `document_chunks.metadata_json` null strategy (902 row) · `audit_log` 0 event `adapchunk%` → **không đo được B2** |

---

## 13. NGUYÊN TẮC SHIP (rút ra từ verify này)

1. **Mọi item PHẢI khai `CONSTANT hay DB?`** trước khi code. Key có trong `system_config` ⇒ sửa constant = **0 tác dụng** ⇒ phải **alembic**.
2. **Mọi item PHẢI khai `ĐÃ TỪNG FIX CHƯA?`**. Có vòng fix-refix (B1 floor 3 lần, D4 ship-mất-vớt) ⇒ **CẤM tune lại số**, phải sửa **cơ chế**.
3. **Không đo được ⇒ không claim.** B2 chưa có telemetry ⇒ **thêm telemetry TRƯỚC**, fix sau.
4. **Test đỏ đang tồn tại (X2) phải xử lý trước** — không được ship thêm lên nền đỏ.
5. **Tái dùng pattern đã có** (B1 back-fill = pattern `mmr_filter` 002-D), **không phát minh**.
6. **1 fix / 1 lần đo.** Không gộp — không quy được nhân quả.
7. **Fix có REINDEX (C1) phải tách riêng**, không trộn với fix code.

---

---
---

# PHẦN 2 — CLASS A / D1 / D2 / E / F

---

## 14. 🚨 A5/A6 — CRAG: **FIX EM SHIP HÔM QUA (`5c4fdda`) KHÔNG CHẠY — VÀ LÀM TỆ HƠN**

> **Đây là bằng chứng sống cho đúng cái user cảnh báo: "đừng fix đi fix lại".**
> Em đã fix sai vì **dùng số liệu bị thiên lệch sống sót (survivorship bias)**.

### 14.1 Runtime — con số audit nói ĐÚNG TUYỆT ĐỐI

`request_steps` live:
```
   grade_path      | count | avg_ms | timeout_s | tổng giây ĐỐT
 skip_high_score   |  418  |     0  |           |     0.0
 timeout_fallback  |  306  |  2115  |  2.0/3.0  |   647.2   ← GỌI LLM XONG VỨT KẾT QUẢ
 batch             |   17  |  1637  |           |    27.8   ← grade THẬT DUY NHẤT
                   TOTAL 741  →  17 thật = 2.3%
rewrite_retry      |    1  row, TỪ TRƯỚC TỚI NAY
```

### 14.2 Cơ chế — `adequate=True` khi timeout

`nodes/grade.py:248-267`:
```python
except asyncio.TimeoutError:
    logger.warning("grade_timeout_fallback_to_rerank_order", ...)
    return {
        "graded_chunks": _fallback_graded,
        "retrieval_adequate": True,      # ← ÉP CRAG PASS
        "grade_timeout_fallback": True,
    }
```
`_grade_route` (`routing.py:169`) chỉ đi `rewrite_retry` khi `retrieval_adequate = False`
→ **418 skip + 306 timeout đều BỎ QUA vòng correction theo cấu trúc.** Chỉ 17 grade thật mới có cơ hội trigger.

### 14.3 🔴 FIX HÔM QUA THẤT BẠI — ĐO ĐƯỢC

`5c4fdda` nâng `DEFAULT_GRADE_TIMEOUT_S` **2.0 → 3.0**, lý do ghi trong code: *"nằm ngay TRÊN p95 đo được (2.56s)"*.

**Runtime SAU fix:**
```
30 lần grade ở timeout_s = 3.0   →   30 TIMEOUT, 0 THÀNH CÔNG   (avg 3015ms)
```

**Vì sao thất bại — SURVIVORSHIP BIAS:**
> "p95 = 2.56s" được tính **CHỈ TRÊN NHỮNG LẦN GRADE HOÀN TẤT** — tức là chỉ những lần **thắng được cap 2.0s cũ**.
> **306 lần timeout là dữ liệu BỊ KIỂM DUYỆT PHẢI (right-censored)** — latency thật của chúng **không biết**, chỉ biết ≥ 2.0s.
> **KHÔNG THỂ ước lượng p95 từ một mẫu bị cắt cụt tại p5.** Phân phối thật kéo dài quá 3s.

**Hậu quả**: fix của em **không cứu được lần grade nào**, mà **tăng thời gian đốt** từ 2s → 3s mỗi query.
→ **Đây là REGRESSION em tự gây ra. Phải xử lý.**

### 14.4 `rewrite_retry` — lần chạy DUY NHẤT cũng là bình phong

Row duy nhất, nguyên văn:
```
2026-07-03 | 5ms | {"attempt":1, "triggered_by":"grade_low", "n_chunks_after":20,
  "original_query_preview":  "Mình có thể thanh toán bằng thẻ tín dụng được không?",
  "rewritten_query_preview": "Mình có thể thanh toán bằng thẻ tín dụng được không?"}
```
Query "đã viết lại" **GIỐNG HỆT TỪNG BYTE** query gốc, sinh ra trong **5ms** (quá nhanh để là 1 LLM call).

→ **CRAG CHƯA BAO GIỜ CHẠY THÔNG END-TO-END TRONG HỆ THỐNG NÀY.**

### 14.5 Trạng thái hiện tại **TỆ HƠN CẢ HAI PHƯƠNG ÁN**

- **Tệ hơn CRAG hoạt động**: cùng chi phí, **0 lợi ích**
- **Tệ hơn không có CRAG**: **thuế latency thuần** — 306 × ~2.1s = **647 giây đốt sạch** + provider vẫn tính tiền token

### 14.6 Tách bạch: 418 skip = **QUYẾT ĐỊNH** · 306 timeout = **TAI NẠN**

- `skip_high_score` (418) — `system_config.crag_skip_retry_above_score = **0.55**` (constant là 0.7 — **DB HẠ XUỐNG**, tức cố ý skip **NHIỀU HƠN**). Tối ưu latency có chủ đích, có document (`grade.py:113-118`).
- `timeout_fallback` (306) — **41% timeout rate = component HỎNG, không phải chính sách.**

### 14.7 2 phát hiện phụ

1. **`model_used` của grader = `openai/claude`** — cặp provider/model **dị dạng**. Đáng nghi là nguồn latency.
2. **Mọi grade row đều có `input_tokens=0, output_tokens=0, cost_usd=0`** — kể cả path `batch` thành công → **chi phí thật của CRAG hiện VÔ HÌNH.**

### 14.8 FIX ĐÚNG — **ĐO LẠI TRƯỚC, TUYỆT ĐỐI KHÔNG TUNE TIẾP**

1. **CẤM tin con số 2.56s.** Phải **đo lại latency KHÔNG BỊ CẮT CỤT**: chạy 1 load-test với grader **không timeout**, lấy phân phối thật.
2. Chi phí CRAG hoạt động (**đo được, không đoán**): `grade_use_batch=true` → **1 LLM call BATCH / query** (≤50 chunk), **không phải 1/chunk**. `pipeline_max_grade_retries=1` → correction loop thêm tối đa **1 rewrite + 1 re-retrieve + 1 re-grade**. Worst case **+3 LLM call**, typical **+1**.
3. Sau khi có số thật → **2 lựa chọn**: nâng cap đủ để grade hoàn tất, **HOẶC** `grade_timeout_s = 0` (tắt hẳn) để **thu hồi 647s**.
4. Điều tra `model_used=openai/claude` + wire token accounting cho grader.

---

## 15. 🔥 A1 — PARSER REGISTRY CHẾT TRÊN LUỒNG PRODUCTION ✅ CONFIRMED — **BUG #1**

> **Đây là bug DUY NHẤT trong 9 điểm Class A vừa là wiring thuần, vừa có payoff T1, vừa CÓ NẠN NHÂN PRODUCTION ĐÃ XÁC NHẬN.**

### 15.1 Chain

```
ingest_core.py:317      if raw_bytes is not None:        ← GATE
ingest_core.py:320          _route_through_parser(...)   ← nguồn DUY NHẤT của parser_row_chunks
ingest_stages.py:763    if parser_row_chunks and _parser_is_row_shaped:
                            _chunking_strategy = "parser_preserve"
                        else:
                            smart_chunk(...)             ← LUÔN LUÔN RƠI VÀO ĐÂY
```

**Worker KHÔNG BAO GIỜ truyền `raw_bytes`** — `interfaces/workers/document_worker.py:514`:
```python
full_text = "\n\n".join(c["content"] for c in _chunks ...)   # ← TỰ PARSE RỒI LÀM PHẲNG
...
doc_service.ingest(... content=full_text ...)                # :668-681 — KHÔNG có raw_bytes
```

```bash
$ grep -c "raw_bytes" src/ragbot/interfaces/workers/document_worker.py
0
$ grep -rn "raw_bytes=" src/ragbot/
ingest_core.py:566                              (đệ quy nội bộ)
interfaces/http/routes/sync.py:566              ← route sync CŨ
interfaces/http/routes/test_chat/document_routes.py:521   ← UI test NỘI BỘ
```

### 15.2 🎯 **VÌ SAO BUG NÀY TRỐN ĐƯỢC LÂU THẾ**

> **UI test nội bộ TRUYỀN `raw_bytes`. API production B2B thì KHÔNG.**
> → Dev test thấy chạy đúng. Khách hàng thật nhận chunking phẳng.

### 15.3 Backward-trace runtime — 0/583 chunk được row-parse

```
 tool_name | created    | chunks |   strategy
 xe-3      | 2026-07-06 |   187  | recursive
 xe-1      | 2026-07-06 |   207  | recursive
 xe-2      | 2026-07-06 |     1  | recursive
 22112     | 2026-06-30 |     1  | recursive
 (csv)     | 2026-06-30 |   187  | recursive
```
`GoogleSheetsParser.supports()` = True cho `text/csv`, và `google_sheets ∈ _ROW_PRESERVE_PROVIDERS` → **cả 5 doc ĐÁNG LẼ phải row-chunk. 0/583 chunk được.**

### 15.4 ⚠️⚠️ **FIX TRƯỚC ĐÓ ĐÃ BỊ CHÍNH BUG NÀY VÔ HIỆU HÓA**

`de89da8` (2026-07-01) `fix(ingest): P2 whole-doc must yield to row-shaped parser (col_N on small sheets)` — commit message nguyên văn:

> *"Live xe-bot bug 2026-07-01: một Google-Sheet markdown 3077 ký tự … gộp 63 chunk google_sheets một-hàng-một-chunk thành MỘT chunk. Stats extractor sau đó **mất binding header per-row → mọi cột rơi về `col_N`**"*

Fix đó gate trên `_parser_row_shaped(parser_row_chunks)` — **mà biến này LUÔN `None` trên luồng worker → fix ĐÓ CŨNG LÀ CODE CHẾT.**

**Bằng chứng đóng đinh**: `xe-1/xe-2/xe-3` ingest ngày **2026-07-06 — 5 NGÀY SAU khi `de89da8` ship** — vẫn là `recursive`. Doc `22112` (1 chunk / 3077 ký tự) **chính là doc được nêu tên trong commit message đó**, đến giờ vẫn chưa fix.

→ **Có người đã đụng đúng hậu quả của A1, vá triệu chứng ở tầng dưới, và khoảng trống wiring âm thầm nuốt luôn bản vá.**
→ Và `col_N` corruption chính là **lớp bug bịa số** mà cả chương trình ADR-0008 đang đuổi theo.

### 15.5 FIX ĐÚNG

Worker truyền `raw_bytes=_raw`, bỏ `"\n\n".join(...)` flatten.

- **Nguồn gốc**: gate = KẾ THỪA (`cd08119`). `git log -S'raw_bytes' -- document_worker.py` → **RỖNG**. Worker **CHƯA BAO GIỜ** truyền. Không có commit nào gỡ wiring — **nó chưa từng tồn tại.**
- **Blast radius**: nhỏ, đo được — **5 doc CSV / 583 chunk** (corpus live không có xlsx/sheets mime). Re-ingest idempotent (`X-Idempotency-Key` + safe-replace).
- ⚠️ **CẦN RE-INGEST** — chunk vật chất hóa lúc ingest. Doc cũ giữ `recursive` cho tới khi re-ingest.
- **Rủi ro**: row-as-chunk **tăng số chunk** (63 hàng → 63 chunk) → tăng chi phí embed/doc.
- ⚠️ **A1 ĐỔI CHUNKING CỦA CORPUS → VÔ HIỆU HÓA MỌI PHÉP ĐO TRƯỚC NÓ.** → **PHẢI LÀM ĐẦU TIÊN.**

---

## 16. 🔴 A4 — `IdempotencyService` **REFUTED — AUDIT CỦA EM SAI**

### 16.1 Em grep sai tên thuộc tính

```bash
$ grep -rn "_idempotency\.\|idempotency\.check\|idempotency\.set" src/
(rỗng)                              ← em kết luận "0 caller" TỪ ĐÂY
```

**Tên thật là `self._idem`.** Grep theo **TÊN METHOD** thay vì tên thuộc tính đoán mò:

```bash
$ grep -rn "is_duplicate\|\.register(\|get_prior_result_ref" src/
application/use_cases/answer_question.py:68   if await self._idem.is_duplicate(idem_key):
application/use_cases/answer_question.py:69       prior = await self._idem.get_prior_result_ref(idem_key)
application/use_cases/answer_question.py:139  await self._idem.register(idem_key, result_ref=str(job_id))
application/use_cases/ingest_document.py:78   if existing is None and await self._idem.is_duplicate(idem_key):
application/use_cases/ingest_document.py:79       prior = await self._idem.get_prior_result_ref(idem_key)
application/use_cases/ingest_document.py:147  await self._idem.register(idem_key, result_ref=str(job_id))
```

`IdempotencyService` có **đúng 3 method** — **cả 3 đều được dùng, trên CẢ luồng chat LẪN luồng ingest.** DI live ở `bootstrap.py:542`.

Còn có **service thứ 2** riêng biệt: `IngestIdempotencyService` (DB-backed) — dùng ở `routes/documents.py:133,140` + `document_worker.py:724,795`. **CẢ HAI cơ chế idempotency đều đang sống.**

### 16.2 Hậu quả nếu em ship theo audit

**Xóa nó = phá vỡ retry-safety BE-to-BE trên cả 2 luồng.** Đây là **hạ tầng correctness chịu tải.**

### 16.3 📌 BÀI HỌC PHƯƠNG PHÁP — GHI VÀO QUY TRÌNH

> **CẤM grep theo TÊN THUỘC TÍNH ĐOÁN MÒ. PHẢI grep theo TÊN METHOD / TÊN SYMBOL.**
> Một lỗi grep duy nhất đã sinh ra 1 finding "dead code" GIẢ trên hạ tầng chịu tải.

---

## 17. A2 / A3 / A7 / A8 / A9 — **PHẦN LỚN KHÔNG ĐƯỢC HỒI SINH**

| # | Verdict | Chết do TAI NẠN hay QUYẾT ĐỊNH? | Khuyến nghị |
|---|---|---|---|
| **A2** `rrf_round_robin` | ✅ CONFIRMED 0 caller | **TAI NẠN** — docstring tự thú: *"safe to wire into the retrieve node **later** (S2 owns query_graph.py)"* → bàn giao cho workstream không bao giờ làm | ⚠️ **ĐO TRƯỚC, ĐỪNG WIRE MÙ.** Hiện **KHÔNG có bằng chứng runtime** nào cho thấy entity-starvation đang hại answer. Chạy 1 load-test intent `comparison`; có starvation → wire với `per_entity_quota` default 0 (no-op); không có → **XÓA** |
| **A3** `null_embedder` | ✅ CONFIRMED file bị comment · 🔴 **TÁC ĐỘNG EM NÓI LÀ SAI** | **QUYẾT ĐỊNH có document** — header file: *"DEAD-CODE NOTICE 2026-06-03 … AST import-graph reachability scan … Safe to delete physically"* | 🗑️ **XÓA.** Registry **ĐÃ degrade an toàn** không cần Null Object: `registry.py:93` → `cls = _REGISTRY.get(key, _REGISTRY[DEFAULT_EMBEDDING_PROVIDER])` → rơi về embedder THẬT, **không bao giờ raise**. Và bản commented **RAISE `EmbeddingError` mọi lời gọi** → **vi phạm chính hợp đồng Null-Object của CLAUDE.md** ("Null Object … KHÔNG raise"). **Wire nó vào = có hại** |
| **A7** `neighbor_expand` | ✅ OFF · 🔴 "chưa wire" **REFUTED** — nó **CÓ trên edge vô điều kiện**, chạy mọi query rồi early-return `{}` | QUYẾT ĐỊNH (config) nhưng **YẾU** — `DEFAULT_NEIGHBOR_EXPAND_ENABLED=False`, **không có row `system_config`**, **0 bot override**. **Không tài liệu nào nói đã ĐO rồi loại** | 🧪 **THÍ NGHIỆM T1 TỐT NHẤT hiện có** — docstring: *"cửa sổ context rộng hơn cho LLM **KHÔNG cần** thêm embedding hay LLM call — chi phí là **1 SQL round-trip batched**"*. **+0 LLM call.** Nhưng **LÀM A1 TRƯỚC** — A1 đổi chunking thì phải đo lại |
| **A8** `critique_parse` | ✅ OFF · "chưa wire" REFUTED (trên edge vô điều kiện) | **QUYẾT ĐỊNH ĐÚNG** | 🚫 **ĐỂ NGUYÊN OFF.** **KHÔNG BẬT ĐƯỢC BẰNG FLAG** — nó cần bot owner **tự thêm rule `[Supported]`/`[Unsupported]` vào `bots.system_prompt`**. Bật flag mà không có rule → LLM không phát token → parse 0 marker → fail open. **Sacred #10 CẤM application tự inject rule đó.** Giữ code (feature opt-in hợp lệ), **đừng hồi sinh từ phía platform** |
| **A9** `reflect` | ✅ 0 step | 🔴 **QUYẾT ĐỊNH CÓ SỐ ĐO — HỒI SINH = TÁI TẠO REGRESSION** | 🚫 **TUYỆT ĐỐI KHÔNG BẬT.** `routing.py:201-206` nguyên văn: *"Production audit (req 9cf611b5) found reflect firing **2x per turn (3.57s wasted)** on bots that never enabled it."* → **GIỮ NGUYÊN COMMENT NÀY.** Nó là **trí nhớ thể chế** sống sót qua đợt re-init repo **chỉ nhờ nằm trong comment**. Audit tương lai nào gắn cờ "reflect chết" phải bị đập lại bằng chính nó |
| **A9** `graph_retrieve` | ✅ 0 step | QUYẾT ĐỊNH — tắt ở **3 tầng độc lập**: `routing.py:234` (`graph_rag_mode == "disabled"` → `return "rerank"`), `system_config.graph_rag_default_mode = "disabled"`, `graph_rag_entity_extraction_model = ""` (rỗng) | 🚫 ĐỂ NGUYÊN. **Không có knowledge graph nào để retrieve.** Bật = cần LLM call **PER CHUNK lúc ingest** (bão token spreadsheet) + KG storage → **T2-âm nặng**. Đây là **quyết định cấp chương trình**, không phải fix wiring |

---

## 18. D1 — HNSW ✅ CONFIRMED SỰ KIỆN · 🔴 **ROOT CAUSE EM NÓI LÀ SAI**

### 18.1 Sự kiện: đúng

```
 document_chunks | idx_chunks_search_vector  |  idx_scan = 19020
 document_chunks | ix_chunks_embedding_hnsw  |  idx_scan =     0
```
`EXPLAIN ANALYZE` query dense thật (`pgvector_store.py:329-336`):
```
Limit  (cost=297.47..297.50 rows=10) (actual time=9.616..9.620)
  -> Sort  (cost=297.47..298.50 rows=413)
       Sort Key: ((embedding <=> '[...]'::vector))
       -> Seq Scan on document_chunks  (cost=0.00..288.55)
```

### 18.2 🔴 Root cause: **KHÔNG PHẢI** 4 giả thuyết em đưa ra

| Giả thuyết em nêu | Verdict |
|---|---|
| Sai opclass | ❌ Index dùng `vector_cosine_ops`, query dùng `<=>` (cosine). **KHỚP** |
| Sai cột | ❌ `DEFAULT_EMBEDDING_COLUMN = "embedding"`, index trên `embedding`. Không có `embedding_v3` |
| Filter `WHERE record_bot_id` chặn pushdown | ❌ **PHẢN CHỨNG QUYẾT ĐỊNH**: agent chạy lại **BỎ HẲN filter** → planner **VẪN chọn Seq Scan** (cost 285.45). Nếu filter là nguyên nhân thì bỏ filter phải kích hoạt HNSW. **Không.** |

**ROOT CAUSE THẬT: cost model của planner ở kích thước bảng này.**
```
906 row.  Seq-scan + sort  = cost 285
          HNSW startup     = cost 5475.29   ← ước lượng THỪA 19×
```
**Planner ĐÚNG về mặt số học khi từ chối index.**

Index **vẫn dùng được**: ép `enable_seqscan=off` → `Index Scan using ix_chunks_embedding_hnsw` **2.48 ms** (nhanh hơn seq scan 13ms). **Cost model sai, không phải index sai.**

### 18.3 KHÔNG có thiệt hại khách hàng HÔM NAY

Agent **tự sửa cách đọc của chính mình**: ban đầu thấy `rows=0` (khi ép HNSW) tưởng là vỡ live. **Không phải.** Probe per-bot (dùng chính vector corpus của bot làm query) → **10/10 cho cả 6 bot ở cả 2 mode**.
→ Planner không bao giờ chọn HNSW ⇒ **seq scan = CHÍNH XÁC 100% recall.** **KHÔNG mất recall.**

**Vách đá tiềm ẩn CÓ THẬT và đã chứng minh được**: ép HNSW + query vector rơi vào vùng của bot khác:
- `iterative_scan=off` → **rows=0** ("Rows Removed by Filter: 71") — **sập recall hoàn toàn, trả về im lặng như "không có chunk"**
- `iterative_scan=relaxed_order` → **rows=10** ✔ fix hoạt động

Ngoại suy đường cost (285/906 row vs 5475 startup): planner **lật sang HNSW ở khoảng ~17k chunk** — **đây là NGOẠI SUY, không phải số đo.**

### 18.4 🔴 DEFECT THẬT = **COMMENT NÓI DỐI** (3 chỗ)

`pgvector_store.py:226-238`:
> *"planner có thể push VÀO toán tử HNSW. Trước 0108 filter này nằm sau subquery `record_document_id IN (SELECT …)` khiến planner không kích hoạt được HNSW (**bằng chứng live: `ix_chunks_embedding_hnsw idx_scan = 0` trên index 22MB**). … **Khi không yêu cầu doc-filter, subquery bị bỏ hẳn và HNSW KÍCH HOẠT.**"*

Nó **trích chính `idx_scan = 0`** làm triệu chứng TRƯỚC-fix, rồi khẳng định fix đã chạy. **`idx_scan` VẪN LÀ 0. Fix đó CHƯA BAO GIỜ chạy.**
Dòng `:257` lặp lại điều đó. Docstring module `:4` ghi "m=16, ef=64" trong khi index thật là **m=32 / ef_construction=200**.
→ **3 lời khẳng định sai lái người đọc tiếp theo đi chệch hướng.**

### 18.5 ⚠️ **ĐÃ TRIAGE RỒI — EM ĐANG PHÁT HIỆN LẠI**

`plans/20260709-remediation-donow/plan.md:13`:
> *"HNSW `idx_scan=0` → **KHÔNG PHẢI BUG** (planner-correct) … HNSW = dead-weight vô hại … **Latent scale-risk** … **KHÔNG ship fix now**."*

**Triage trước ĐÚNG SỰ THẬT và đi tới cùng kết luận.** Cái plan đó **quên làm** là **xóa cái comment nói dối** — và chính comment đó giữ cho chẩn đoán sai sống mãi.

### 18.6 FIX ĐÚNG (rẻ, an toàn)

1. **XÓA/SỬA comment sai** ở `:4`, `:226-238`, `:257` — **đây MỚI là defect thật**, vì nó sẽ khiến kỹ sư tiếp theo chẩn đoán sai (đúng như nó vừa khiến em chẩn đoán sai).
2. Thêm `SET LOCAL hnsw.iterative_scan = 'relaxed_order'` cạnh `SET hnsw.ef_search` đã có (2 call site: `:322`, `:404`), lấy từ `system_config` theo zero-hardcode. **Hôm nay là no-op**, và là **lan can đã dựng sẵn** khi cost crossover tới.
3. **KHÔNG ép index hôm nay.** Planner đang đúng.

*(pgvector = **0.8.1** → `hnsw.iterative_scan` có sẵn. Grep `src/` → **0 hit**, xác nhận.)*

---

## 19. D2 — SWAP EMBEDDER CÙNG DIM 🟡 PARTIAL

### 19.1 Cơ chế: ✅ CONFIRMED, KHÔNG CÓ GUARD

`alembic/versions/20260626_embed_swap_to_openai.py` — toàn bộ `upgrade()` là **3 câu `UPDATE system_config`** (provider→`litellm`, model→`text-embedding-3-small`, dimension→`1024`).
**KHÔNG re-embed, KHÔNG null, KHÔNG xóa MỘT vector nào.**

Docstring của chính nó (dòng 11-13) **tự thú khoảng trống**:
> *"**REQUIRES re-embedding the corpus**: existing vectors are Jina-1024 (different space), so all 3 bots must be re-ingested after this migration."*

**Yêu cầu đó được cưỡng chế bởi: KHÔNG GÌ CẢ, ngoài cái docstring.**

`_check_embed_model_consistency` (`query_graph.py:754`) — docstring nguyên văn: *"Detect query vs ingest embedding model mismatch. **Detection-only, never raises.**"* Log warning, tăng counter, `return True`. Tại call site duy nhất (`:1635`) **giá trị trả về BỊ VỨT** — nó là một expression statement trần trụi.

### 19.2 🔴 Lỗ hổng SÂU HƠN mà cả em lẫn audit đều bỏ sót

Check này so `_pcfg(state, "embedding_model")` (giá trị `system_config` **HIỆN TẠI**) với `spec.model_name` (model resolve **lúc query**). **Cả hai đều dẫn xuất từ CÙNG một config.**
→ Sau khi swap, **chúng KHỚP NHAU** → check trả `False` → **trong khi vector đã lưu là của model CŨ.**

> **Nó BẤT LỰC VỀ MẶT CẤU TRÚC trong việc phát hiện đúng cái failure mà nó được đặt tên để phát hiện.** Nó chỉ bắt được bất đồng resolver/config, **KHÔNG BAO GIỜ** bắt được vector cũ.

**Không có cột provenance**: `document_chunks` có 16 cột, **không cột nào** ghi embedding model / provider / version / dimension.

### 19.3 🔴 Thiệt hại LIVE: **REFUTED**

```
 record_bot_id | chunks | oldest     | tạo TRƯỚC swap | tạo SAU swap
 (bot 1)       |   403  | 2026-07-06 |             0  |      403
 (bot 2)       |   187  | 2026-06-30 |             0  |      187
 … cả 6 bot                          |             0  |     (all)
```
**Toàn bộ 906 chunk đều sau ngày swap 2026-06-26.** Và swap OpenAI **đã bị thay thế**: config live là `zeroentropy / zembed-1 / 1280`, vector lưu đúng 1280 (`vector_dims` = 1280 cho cả 906; typmod 1280; `semantic_cache.query_embedding` cũng 1280 trên 391 row). **Không có drift dimension ở đâu cả.**

**Thứ đã cứu họ — sự bất đối xứng**: swap sang ZE đổi width **1024 → 1280**, mà pgvector **hard-fail** với sai width → **tự phát hiện**. Chỉ bước **jina-1024 → OpenAI-1024** là cùng width nên **im lặng**. **Cơ chế mà audit mô tả là CHÍNH XÁC.**

### 19.4 Phát hiện thật: **CHỈ BIẾT ĐƯỢC NHỜ MAY MẮN**

Corpus sạch **CHỈ VÌ** `created_at` tình cờ sau ngày swap — mà ta chỉ kiểm tra được điều đó **vì re-ingest tạo lại row**.
→ **KHÔNG CÓ CÁCH NÀO chứng minh provenance của MỘT vector cụ thể.** Nếu 1 bot không được re-ingest, **không gì trong hệ thống bắt được**, và retrieval sẽ "chạy bình thường" trên **không gian nhúng ngoại lai**.

### 19.5 FIX ĐÚNG

Thêm `embedding_model VARCHAR` + `embedding_dim INT` vào `document_chunks`, ghi lúc ingest.
Đổi consistency check thành: so model **query** với **model GHI TRÊN CHÍNH ROW ĐƯỢC RETRIEVE** — **không phải với config** — và **fail loud** khi lệch.
→ Khi đó swap cùng-width mà không re-embed sẽ thành **lỗi lúc startup/query**, thay vì sụp chất lượng im lặng.
**Mọi phương án khác chỉ là docstring.**

- **Blast radius**: thêm cột = **thuần bổ sung** (nullable, backfill từ `created_at` đối chiếu ngày migration). **Phần rủi ro là làm check fail-loud** — nó sẽ hard-fail query trên row legacy không rõ provenance → **phải gate (warn → block) sau per-bot flag.**

---

## 20. 🚨 E1 — CACHE HIT BỎ QUA `guard_output` ✅ **CONFIRMED — LỖ HỔNG AN NINH LIVE**

> **Đây là item DUY NHẤT vừa CONFIRMED, vừa LIVE, vừa ẢNH HƯỞNG KHÁCH HÀNG. Ship trước.**

### 20.1 Nửa 1 — short-circuit (chứng minh bằng TOPOLOGY, không phải suy luận)

`nodes/routing.py:56-59`:
```python
def _cache_route(state: GraphState) -> str:
    """If cache hit produced an answer, skip to persist."""
    if state.get("cache_status") == "hit" and state.get("answer"):
        return "persist"
```
`query_graph.py:2964-2968` nối dây:
```python
graph.add_conditional_edges(
    "cache_check_and_understand_parallel",
    _cache_route,
    {"persist": "persist", "understand_query": "understand_query", "condense_question": "condense_question"},
)
```
`persist → END` (`:3038`). `guard_output` nằm **hẳn trên nhánh kia** (`generate → critique_parse → guard_output`, `:3026-3027`).

→ **Cache hit chạy thẳng tới END sau khi thực thi ZERO output guard.**

### 20.2 Nửa 2 — cache key KHÔNG chứa guardrail

`application/ports/cache_port.py:90`:
```python
return f"t:{record_tenant_id}:bot:{record_bot_id}:bv:{bot_version}:cv:{corpus_version}"
```
**Không có thành phần guardrail nào.** Và `bot_version` = `_compute_bot_cache_version` (`query_graph_helpers.py:130-161`) có **đúng 3 input**: `system_prompt`, `oos_answer_template`, `custom_vocabulary`. **Guardrail rule KHÔNG phải input.**

Đóng vòng: `GuardrailRuleLoader.invalidate()` (`guardrail_rule_loader.py:296-324`) **chỉ** xóa L1 cache của chính nó + publish `SUBJECT_GUARDRAIL_RULES_CHANGED`. **KHÔNG BAO GIỜ đụng `semantic_cache`.** Grep xác nhận: 0 tham chiếu tới semantic cache trong module đó.

### 20.3 Cửa sổ bypass = **3600s** (khớp chính xác)

`DEFAULT_SEMANTIC_CACHE_TTL` (`_04_jwt_auth.py:13`) = 3600.

**Kịch bản**: owner thêm rule BLOCK → **mọi query đã cache tiếp tục phục vụ câu trả lời cũ, giờ đã bị cấm, KHÔNG QUA GUARD, tới 1 tiếng** (lâu hơn nếu TTL refresh theo hit), và **không có đường invalidation nào để rút ngắn.**

### 20.4 Nguồn gốc

- **KẾ THỪA** (`cd08119`) cả `_cache_route` lẫn `_compute_bot_cache_version`. Các commit sau (`8435c17`, `8fdba55`, `5a515c5` — đều 2026-06-19) là **refactor extract thuần**, không đổi hành vi.
- **FIX-REFIX: KHÔNG** — chưa từng fix, chưa từng regress, chưa từng đụng lại.
- **KHÔNG cố ý** — docstring *"If cache hit produced an answer, skip to persist"* mô tả hành vi **mà không thừa nhận việc bỏ qua guard**. Không ADR, không comment, không plan.

### 20.5 FIX ĐÚNG — **2 phần, cần CẢ HAI**

1. **Định tuyến cache hit QUA `guard_output` trước khi `persist`.** Guard phải nằm trên **MỌI đường phát ra câu trả lời**, không chỉ đường generate. → **Guard mà bỏ qua được thì không phải guard.** ← **fix lỗ hổng**
2. **Nhét hash của ruleset guardrail đã compile vào `_compute_bot_cache_version`** → sửa rule là bust key. ← **fix tính đúng đắn của hợp đồng cache-coherence**

(1) một mình đã bịt lỗ hổng. (2) một mình **chỉ thu hẹp cửa sổ**. **Ưu tiên (1).**

- **Blast radius**: (2) sẽ **invalidate toàn bộ cache 1 lần khi deploy** (flush lạnh 1 lần — lưu ý code `:155-158` đã cố tránh đúng điều này cho field vocab). (1) thêm latency guard vào fast-path — nhưng output guard **phần lớn là local/regex** → chi phí nhỏ. **Cả hai đều bị chặn trong phạm vi hẹp.**

---

## 21. E2 — XML-WRAP INJECT ✅ CONFIRMED CẢ 3 CÂU HỎI

### 21.1 Code

`nodes/generate.py:630, 663-675, 710`:
```python
_xml_wrap = _resolve_xml_wrap_enabled(state)
...
    context_blocks.append(
        f'<chunk id="{cid}" type="{_ctype}" section="{_section}">\n'
        f'<content>{text}{_vfence}</content>\n'
        f'</chunk>')
elif _trust_hint:
    context_blocks.append(
        f'<context source="{source_label}" ... trust="data_only" ...>\n{text}{_vfence}\n</context>')
...
_user_content = f"<documents>\n{context_str}\n</documents>\n\n<question>{_q}</question>"
```

### 21.2 (a) Gate theo NGÀY SINH — ✅ CÓ

`query_graph.py:587-601`, docstring nguyên văn:
> *"2. `bot_created_at >= XML_WRAP_DEFAULT_ON_FROM_DATE` — bots created on/after the cutoff **default to True when the key is absent**."*

`XML_WRAP_DEFAULT_ON_FROM_DATE: Final[str] = "2026-05-18"` (`_00_app_env_taxonomy.py:113`)

### 21.3 🔴 Blast radius LIVE — **4/6 bot đang bị XML-wrap MÀ KHÔNG AI BIẾT**

```
 bot          | created_at | qua cutoff | owner có set không
 (bot A)      | 2026-05-07 |     f      |        f
 (bot B)      | 2026-05-13 |     f      |        f
 (bot C)      | 2026-06-11 |     t      |        f     ← BẬT vì NGÀY SINH
 (bot D/E/F)  | 2026-06-30 |     t      |        f     ← BẬT vì NGÀY SINH
```
**KHÔNG MỘT owner nào set `xml_wrap_enabled`.** 4 bot bị bật **chỉ vì thời điểm row bot được tạo**.

> **Hai bot GIỐNG HỆT NHAU, chỉ khác ngày tạo, nhận PROMPT KHÁC NHAU.**

### 21.4 (b) Owner có thấy không? — ❌ **KHÔNG**

`admin_bots.py:192-207` (`GET /admin/bots/{id}/effective-prompt`) trả `base_prompt` / `platform_appended` / `effective_prompt` / `disabled_rule_ids` — **tất cả dẫn xuất từ SYSTEM prompt** qua `SysPromptAssembler`.
**XML wrap được inject vào USER message.** → **VÔ HÌNH với endpoint này.**

Docstring của chính endpoint (`:215-219`) nói nó tồn tại để thỏa *"ADR-W1-S10 điều kiện 1 — platform-rule append CHỈ được phép khi owner soi được chính xác cái gì bị append."*
→ **XML wrap là một sửa đổi prompt do platform viết ra, và nó THOÁT KHỎI hợp đồng minh bạch đó.**

### 21.5 (c) ADR? — ❌ **KHÔNG CÓ**

`docs/adr/` có 0001–0008. Grep `xml_wrap` / `trust="data_only"` → **0 hit.**

### 21.6 ⚖️ Cân nhắc CÔNG BẰNG mức độ nghiêm trọng (agent tự hiệu chỉnh)

Bọc `<documents>`/`<question>` (`:710`) là **VÔ ĐIỀU KIỆN**, có **TRƯỚC** cái flag, và **chính sysprompt template của platform tham chiếu tới nó** (`context_aware_refusal_template.py:80-97`: *"…`<documents>...</documents>`. Use only that material; never fabricate facts."*).
→ Tag đó là **HỢP ĐỒNG CẤU TRÚC** giữa platform và prompt, **không phải rule lậu**. Người công tâm sẽ gọi việc đóng khung context là **"phong bì giao hàng"**, không phải **"rule bị nhét vào"**.

**Token thật sự MANG RULE là `trust="data_only"`** — đó là **chỉ thị ngữ nghĩa cho LLM do application viết ra.**

**Nhưng gate-theo-ngày là thất bại quản trị BẤT KỂ phân loại thế nào.**

### 21.7 Nguồn gốc

**KẾ THỪA** — `git log -S "XML_WRAP_DEFAULT_ON_FROM_DATE"` → **chỉ `cd08119`**. `git blame :663-667` → `cd08119`.
→ **Không có dấu vết phê duyệt nào, vì commit khai sinh nó chính là initial import.** Git **không thể** cho biết nó từng được tranh luận hay chưa. FIX-REFIX: thấp (1 lần đụng cạnh trong 26 ngày, không revert).

### 21.8 FIX = **QUYẾT ĐỊNH, không phải code**

| | Phương án | Chi phí | Đánh giá |
|---|---|---|---|
| **A** ⭐ | **Viết ADR, GIỮ hành vi.** Codify thành ADR-0009 dưới đúng 4 điều kiện của ADR-W1-S10: (a) seed qua migration tracked, (b) domain-neutral, (c) per-bot opt-out (**đã có** qua `plan_limits.xml_wrap_enabled`), (d) **owner XEM ĐƯỢC** → phải **mở rộng `effective-prompt` để render CẢ user message**. Rồi **XÓA gate-theo-ngày**, thay bằng default per-bot ghi lúc tạo bot | 1 ADR + 1 mở rộng endpoint + 1 migration backfill flag cho 4 bot | **KHUYẾN NGHỊ** |
| B | **Gỡ wrap.** Bỏ `_xml_wrap` + `trust="data_only"`, chỉ giữ `<documents>` envelope mà sysprompt template đã phụ thuộc | **Rủi ro regression chất lượng chắc chắn** trên 4 bot live, **PHẢI A/B**. Và `<documents>` envelope **vẫn phải biện minh** vì template tham chiếu nó | Đắt |

⚠️ **Đổi default = đổi nội dung prompt của 4 bot live → answer sẽ đổi → mọi baseline golden/load-test PHẢI đo lại.** Đây là thay đổi **ảnh hưởng T1-smartness** và **CẤM ship không A/B** (rule #0).

🔒 **Giết cái gate-theo-ngày là KHÔNG THƯƠNG LƯỢNG, bất kể chọn A hay B**:
> **Prompt của một con bot KHÔNG BAO GIỜ được phụ thuộc vào NGÀY SINH của nó.**

---

## 22. 🔴 E3 — GROUNDING FAIL-CLOSED **REFUTED — EM VU OAN CHO CODE ĐÚNG**

### 22.1 Default là `observe`, KHÔNG phải `block`

`_14_...py:325-327`:
```python
GROUNDING_CONFIRMED_ACTION_OBSERVE: Final[str] = "observe"
GROUNDING_CONFIRMED_ACTION_BLOCK:   Final[str] = "block"
DEFAULT_GROUNDING_CONFIRMED_ACTION: Final[str] = GROUNDING_CONFIRMED_ACTION_OBSERVE   # ← OBSERVE
```
`guard_output.py:871` chỉ block khi **opt-in tường minh**.

**LIVE DB xác nhận:**
- `system_config`: **KHÔNG TỒN TẠI key `grounding_confirmed_action`** → rơi về default code = `observe`
- `plan_limits`: **đúng 1 bot** set nó, và set thành **`observe`**. 5 bot còn lại: không set.

→ **KHÔNG bot nào trên platform này block khi confirmed-ungrounded.**

### 22.2 Em nhầm với 1 knob KHÁC

`:314` — `DEFAULT_GROUNDING_FAILURE_MODE = GROUNDING_FAILURE_MODE_FAIL_CLOSED`. Cái **NÀY** mới fail-closed.
Nhưng nó **chỉ bắn khi grounding judge KHÔNG CHẠY ĐƯỢC** (`:301-311`: *"the LLM runtime is unwired … the HALLU net is silently OFF"*), và hành động của nó là **thay bằng chính `oos_answer_template` CỦA BOT**.

→ Theo **CLAUDE.md Application-MINDSET rule #3** (*"Refusal text origin: `bots.oos_answer_template`"*), thay bằng **refusal text của chính bot** là **ĐƯỜNG ĐƯỢC CHUẨN THUẬN**, **không phải app-inject override.**
→ Đây là **tư thế HALLU=0 CHÍNH ĐÁNG**, không phải vi phạm Sacred #10.

### 22.3 📣 **Commit em vu oan thực ra là MẪU MỰC của kỷ luật CLAUDE.md**

`c0c0dea` (2026-07-03) đưa vào **tùy chọn** block **trong khi cố ý default `observe`**, và để lại comment (`:320-324`):
> *"Default là 'observe' để **KHÔNG bot nào bị đổi refuse-rate mà không opt-in tường minh**; owner chỉ flip sang 'block' per-bot **SAU KHI ĐO** rằng độ lệch false-positive cố ý của ngưỡng grounding không over-refuse những câu thật sự grounded."*

**Đây chính xác là thứ CLAUDE.md yêu cầu. Em chê nhầm code tốt.**

### 22.4 App-override LIVE thật sự nằm chỗ khác — và **CÓ OWNER DUYỆT**

1 bot có `plan_limits.numeric_fidelity_action = "block"`.
Commit `f22a808` (2026-07-06): *"feat(config): enable numeric-fidelity BLOCK … (002-I, **owner-approved**)"*

→ Verdict đúng **KHÔNG PHẢI** "vi phạm sacred, không ai duyệt" mà là:
> **"Owner ĐÃ DUYỆT, nhưng chưa bao giờ ghi thành ADR"** — **khoảng trống GIẤY TỜ, không phải override lậu.**

### 22.5 ⚠️ NHƯNG — phát hiện THẬT ở đây: **8 commit guard / 7 ngày, 0 ADR**

| commit | ngày | subject |
|---|---|---|
| `c0c0dea` | 07-03 | A1 grounding block option (default `observe`) |
| `a3529f3` | 07-06 | P4 request trace + P3 numeric-fidelity block **toggle** |
| `f22a808` | 07-06 | **enable** numeric-fidelity BLOCK cho 1 bot (owner-approved) |
| `b5fc6cb` | 07-06 | feed conversation history vào numeric-fidelity grounding |
| `ed26e1b` | 07-06 | Step-17 P1 digit-signature **explored + REVERTED** (zero delta + brand-conflation defect) |
| `7c2570c` | 07-08 | empty-answer guard + claim-fidelity gate + brand-scope **BLOCK** |
| `67b82de` | 07-08 | **enable** empty-answer guard cho 2 bot |
| `9cdd4c6` | 07-09 | numeric-fidelity mù với số điện thoại bịa |

*(Về `ed26e1b`: agent kiểm rồi — nó **chỉ đụng `specs/001-.../evidence/`, 0 file `src/`**. Là **ngõ cụt được ghi lại**, không phải revert code. Nên **không phải** fix-refix loop trong code.)*

**Nhưng pattern thì không thể nhầm lẫn:**
> **Bề mặt app-override đang được NỚI RỘNG từng chút một, từng bot một, với ZERO ADR.**
> 1 bot giờ mang: numeric-fidelity BLOCK + brand-scope BLOCK + empty-answer guard — mỗi cái 1 commit riêng, mỗi cái "owner-approved" **chỉ trong commit message**.

**Plan nào tune mù mấy knob này sẽ THRASH.**

### 22.6 FIX = **1 ADR, 0 code, ~1-2h**

Viết **MỘT ADR** phủ cả họ app-override (`grounding_confirmed_action`, `grounding_failure_mode`, `numeric_fidelity_action`, brand-scope block, empty-answer guard) xác lập:
- chúng thay bằng **`oos_answer_template` CỦA CHÍNH BOT**, không bao giờ dùng text platform
- **per-bot opt-in, default observe**
- **owner approval phải được ghi TRONG ADR**, không chỉ trong commit message

**Phương án thay thế (gỡ guard) = đánh đổi khoảng trống giấy tờ lấy regression HALLU → RÕ RÀNG SAI.**

---

## 23. F1 — MULTI-DOC CONFLICT: 2/4 vế SAI

### 23.1 (a) Key dedup gộp ĐỒNG THUẬN, giữ MÂU THUẪN — ✅ CONFIRMED

`query_graph.py:2620`:
```python
_key = (_name, int(_price) if _price is not None else -1)
if _key in _seen:
    continue
_seen.add(_key)
```
Cùng tên + **cùng giá** → gộp. Cùng tên + **KHÁC giá** → khác `_key` → **CẢ HAI SỐNG**, cả hai được serialize vào synthetic chunk và phục vụ LLM.
→ **Dedup làm ĐÚNG NGƯỢC LẠI conflict resolution.**
**KHÔNG test nào ghim cái này** (xem (d)) → **fix không vỡ gì.**
**Nguồn gốc**: KẾ THỪA (`cd08119`), git im lặng, chưa từng đụng lại.

### 23.2 (b) Synthetic chunk KHÔNG có provenance — ✅ CONFIRMED, và **FIX RẺ HƠN EM TƯỞNG**

`query_graph.py:2649` → `"document_name": ""` · `:2650` → `"score": 1.0`
`_serialize_stats_entity_row` (`:366`) phát ra name/price/category/attrs — **không doc, không ngày**.
→ **LLM thấy N dòng giá mâu thuẫn với ZERO quy kết nguồn.**

**NHƯNG**: `stats_index_repository.py:57` **ĐÃ CÓ SẴN**
```python
_DOC_LIVE_JOIN = "JOIN documents AS d ON d.id = dsi.record_document_id"
```
và **MỌI SELECT** (`:316, :382, :526, :638, :671`) **đã trả về `dsi.record_document_id`**. Live DB: cột đó **NOT NULL**.

> **Provenance chỉ cách 1 CỘT SELECT. KHÔNG migration. KHÔNG re-ingest.**

### 23.3 (c) "Platform từng có conflict-resolution rồi gỡ" — 🟡 **GÂY HIỂU LẦM, PHẢI DIỄN ĐẠT LẠI**

Cột đúng là đã mất (live `information_schema` trên `documents`: **không có** `authority_score` / `valid_from` / `valid_until` / `superseded_by`).

**Nhưng GIT IM LẶNG về ý định**: migration 0010 **đến CÙNG `cd08119`** (initial import), giờ nằm ở `alembic/_archive_pre_squash_20260618/`. Commit message của nó chỉ là *"first commit"*.

**Bằng chứng DUY NHẤT là docstring của chính nó:**
> *"drops **wired-but-unused** columns in `ai_providers` and `documents`."* · *"Safety: **0 production data**"*

và `infrastructure/db/models.py:339-340`:
> *`# Note: authority_score / valid_from / valid_until / superseded_by dropped`*
> *`# in migration 0010 (advanced features not yet wired end-to-end).`*

**KHÔNG CÓ MIGRATION ADD nào tồn tại trong repo** — file alembic duy nhất nhắc tới mấy cột này là cái DROP.

> → **Platform có CÁI CỘT, nhưng CHƯA BAO GIỜ có LOGIC.**
> → Đây **KHÔNG PHẢI** đảo ngược một tính năng đang chạy. Đây là **gỡ giàn giáo chết.**
> → **Khôi phục 4 cột = dựng lại giàn giáo, KHÔNG PHẢI khôi phục năng lực.**

**🔴 Drift domain/DB (phát hiện mới)**: domain layer **VẪN model chúng** — `domain/entities/document.py:99` `authority_score: AuthorityScore`, `:101` `superseded_by: DocumentId | None`, `:157` `superseded_by=replacement`, và `domain/value_objects/versioning.py:36-45` (`valid_until` + invariant).
→ **Entity đang model metadata mà DB không thể lưu.**

### 23.4 (d) "Test dòng 68 ghim mâu thuẫn như tính năng" — 🔴 **REFUTED, RÕ TO**

`tests/unit/test_crossdoc_reconcile.py:68` là `test_two_priced_anchors_never_merged`, và 2 row là **HAI SẢN PHẨM KHÁC NHAU**:
- `a1.entity_name = "2-ZR18 235/40 <BRAND-A>"` (1602000)
- `a2.entity_name = "2-ZR18 235/40 <BRAND-B>"` (1550000)

Hai hậu tố là **mã thương hiệu** — ADR-0008 dùng đúng shape này.

Hàm được test, `_reconcile_cross_doc` (`query_graph.py:441`, thêm ở `aa029ec` 2026-07-02), là **BỘ GỘP MẢNH VỠ**, **không phải bộ giải mâu thuẫn**: *"Merge cross-doc price-LESS fragments INTO the priced anchor."*

Assert `len(out) == 2` ghim rằng **2 sản phẩm PHÂN BIỆT không được hợp nhất** — **ĐÚNG**, và **chính là defect B5 brand-conflation của ADR-0008 mà nó đang canh.** **Gộp chúng lại MỚI là bug.**

Em bị lừa bởi **comment dòng 76** `# both are priced → both kept (a price conflict must never be silently merged)`. **Test KHÔNG ghim F1(a). Hai thứ nằm ở 2 hàm khác nhau.**

### 23.5 ⚠️⚠️ FIX-REFIX RISK: **CAO NHẤT TOÀN AUDIT**

Dòng stats-serve đã bị **vá điểm 7 LẦN TRONG 12 NGÀY**:
```
949a3a4 · aa029ec · d4de411 · ec4a335 · eb750f0 · 2ad4df7 · d495db2
+ ed26e1b "digit-signature route explored + REVERTED (zero delta + brand-conflation defect)"
```
→ **Đây là vòng thrash ĐANG HOẠT ĐỘNG.** Một bản vá điểm **thứ 8** vào `_serialize_stats_entity_row` mà **phớt lờ ADR-0008 sẽ lặp lại đúng vòng đó.**

### 23.6 ADR-0008 ĐANG QUẢN vùng này — và **2 mục nó ra lệnh vẫn CHƯA LÀM**

- **B4** — *"synthetic chunk **KHÔNG ĐƯỢC đè bẹp raw chunk đúng** khi confidence thấp: score của nó phải phản ánh match confidence"* → **`score: 1.0` ở `:2650` CHÍNH LÀ B4**
- **B5** — cross-doc merge phải khớp name/identity

→ **Mọi fix PHẢI được đóng khung là PHẦN MỞ RỘNG của ADR-0008**, không phải patch điểm thứ 8.

### 23.7 FIX ĐÚNG — theo TẦNG

**Tầng 1 (KHÔNG migration, KHÔNG re-ingest):**
1. Thêm `d.document_name` + `d.updated_at` vào SELECT/JOIN **đã có sẵn**
2. Luồn vào entity dict → điền `document_name` cho synthetic chunk
3. Giữ `(_name, price)` để gộp **đồng thuận chính xác**, nhưng **GROUP BY `_name` và phát hiện >1 giá phân biệt = CONFLICT** → phục vụ **CẢ HAI** row **KÈM tên doc + ngày** để LLM tự trích dẫn / tự chọn
4. Phát `stats_price_conflict` structlog/audit event — **hôm nay mâu thuẫn diễn ra HOÀN TOÀN IM LẶNG**
5. Bỏ `score: 1.0` vô điều kiện — theo **ADR-0008 B4**

🔒 **GUARD SACRED-RULE**: **KHÔNG hardcode "mới nhất thắng" trong app** — đó là **app-override câu trả lời (QG#10)**.
> **Ưu tiên độ mới thuộc về `system_prompt` / config của bot owner. App cung cấp DỮ LIỆU (tên + ngày). LLM QUYẾT ĐỊNH.**

**Tầng 2 (chỉ khi owner cần):** authority/validity do owner khai báo thuộc về **manifest per-file của ADR-0008**, **không phải 4 cột hồi sinh bespoke**. `documents.updated_at` + `version` **đã tồn tại** → recency **KHÔNG CẦN cột mới**.

- **Blast radius**: **KHÔNG migration, KHÔNG re-ingest.** Test có thể phải cập nhật format dòng: `test_stats_synthetic_null_price_marker.py`, `test_stats_serve_value_filter.py`, `test_stats_query_attributes_selected.py`.
  **`test_crossdoc_reconcile.py` KHÔNG CẦN đổi — nó đúng.**

---

## 24. F2 — `query_complexity` 2 NHÁNH VÔ DỤNG ✅ CONFIRMED (số liệu em nói hơi sai)

`nodes/query_complexity_node.py:57-62` gather 3 nhánh; `:93` `return merged` — **chỉ nhánh A tới được state.**

- **Nhánh B** `_run_router_select_model` (`query_graph.py:2832`) — docstring: *"No state keys are written — purely observability."* Trả `{}`. **NHƯNG** nó **có** `await model_resolver.resolve_runtime(purpose="understand_query")` (`:2843`) = **1 resolver round-trip THẬT** + 1 row `request_steps`.
- **Nhánh C** `_run_semantic_cache_preflight` (`:2877`) — *"**Does NOT re-query pgvector**… Returns `{}` always."* → 1 row `request_steps`, **0 DB**.

**Sửa lại con số của em**: chi phí là **2** row lãng phí + **1** resolver round-trip — **không phải 3 row**. Row thứ 3 (`query_complexity`) **được `_complexity_route` dùng thật** (`routing.py:93-97`). Và DB hit đến từ nhánh **telemetry**, không phải nhánh "preflight".

- **Nguồn gốc**: **CÓ CHỦ ĐÍCH** — `17eaac6` (2026-06-19) *"refactor(orchestration): extract query_complexity_node + adaptive_decompose (Phase D.7)"* → refactor đó **SONG SONG HÓA sự lãng phí thay vì gỡ nó.**
- **Cố ý?** Có — tự dán nhãn "telemetry only" / "validation only". **Dead-weight ĐÃ BIẾT.**
- **FIX**: xóa cả 2 coroutine + 2 step row. **Telemetry của nhánh B là TRÙNG LẶP** — bước `understand_query` LLM thật đã resolve cùng binding đó. Gather sập còn `await _run_query_complexity(state)`, cho `pipeline_pre_retrieval_parallel_enabled` nghỉ hưu.

---

## 25. F3 — `condense_question` + `router` CHẾT ✅ CONFIRMED (verify LIVE)

`nodes/routing.py:56-62`:
```python
if _pcfg(state, "merge_condense_router", True):
    return "understand_query"
return "condense_question"        # ← đường DUY NHẤT tới condense_question
```
`router` chỉ tới được qua `graph.add_edge("condense_question", "router")` (`query_graph.py:2994`).

**LIVE CONFIG:**
```
system_config.pipeline_merge_condense_router = TRUE      (updated_at 2026-05-05)
per-bot plan_limits override                = 0 ROWS
system_config.query_router_provider         = "null"     ← Null Object
```
→ **Cả 2 node CHẾT trên MỌI query, cho MỌI bot.** 2 LLM call-site: `nodes/condense_question.py:88` + `nodes/router.py:37`.

- **Nguồn gốc**: `8435c17` (2026-06-19, refactor extract), nhưng **default flag + row DB có từ 2026-05-05** → đường merged là đường **DUY NHẤT** sống **>2 THÁNG**.
- 🔴 **VI PHẠM ZERO-HARDCODE tìm thấy**: default `True` **inline ở 3 chỗ** (`routing.py:60`, `chat_worker/pipeline_config.py:468`, `test_chat/_pipeline_config.py:409`) — **KHÔNG có constant nào trong `shared/constants/`**
- **FIX**: xóa **node** `condense_question` + `router`, 2 LLM call-site, và edge.
  ⚠️ **KHÔNG xóa `_router_route`** — nó **VẪN SỐNG**, được gọi bởi `_understand_query_route` (`routing.py:90`) và `_complexity_route` (`:97`).
  Gỡ flag `merge_condense_router` + 3 default inline. **Alembic (KHÔNG psql)** để xóa row `system_config` mồ côi.

---

## 26. F4 — ATOMIC PROTECT OFF ✅ CONFIRMED (flag) · ⚠️ HẬU QUẢ CHƯA VERIFY

```python
_00_app_env_taxonomy.py:126
DEFAULT_FORMULA_IMAGE_ATOMIC_PROTECT_ENABLED: Final[bool] = False
```
Comment `:119-125`: *"Default OFF — flip via `system_config.formula_image_atomic_protect_enabled`. Khi ON, … đánh dấu atomic (`is_atomic=True`) để **MỌI chunking strategy giữ chúng nguyên vẹn**."*

**LIVE**: key **VẮNG MẶT** khỏi `system_config` → default `False`. **0 bot override.**
**Nguồn gốc**: KẾ THỪA (`cd08119`), chưa từng đụng lại.

**Sắc thái phạm vi**: flag gate `_split_into_blocks_with_atomic` (`chunking/__init__.py:329`). Các fast-path bảng (`table_csv` `:318`, `table_dual_index` `:503`) **return TRƯỚC** lời gọi đó → atomic protect áp cho strategy **KHÔNG-phải-bảng** (prose/markdown), **không áp cho doc bảng.**

⚠️ **CHƯA VERIFY**: agent xác nhận flag + default live bằng code + DB, nhưng **KHÔNG chạy chunker trên doc có formula/code để QUAN SÁT một lần cắt giữa block.**
→ **Theo rule #0**: cần **1 failing test** (fenced code block > `chunk_size` → assert không bị split) **TRƯỚC KHI** tuyên bố defect quan sát được và **TRƯỚC KHI** flip default.

---

## 27. 🔴 F5 — INTRO/FOOTER BẢNG ✅ CONFIRMED · **CƠ CHẾ EM NÓI SAI** · **+ PHÁT HIỆN DRIFT DB PROD vs FRESH**

### 27.1 Feature **TỒN TẠI VÀ ĐANG BẬT**

`csv_chunker.py:250-355` — `_chunk_table_csv_with_context` phát chunk header (`region.pre` + header + N hàng đầu, `:315-317`) và chunk footer (header + N hàng cuối + `region.post`, `:341-343`).
Docstring `:270-277`: *"synthetic chunk cho phép retrieval nổi lên phần tổng quan chủ đề … và ghi chú cuối … thứ mà row-as-chunk thuần **VỨT BỎ**."*
**LIVE**: `table_csv_emit_header_footer_chunks_enabled = **true**` (2026-05-25).

### 27.2 🔴 **NHƯNG STRATEGY LIVE KHÔNG PHẢI `table_csv`**

```
system_config.chunking_policy = {"table_strategy": "table_dual_index"}   (2026-06-12)
```
`_chunk_table_dual_index` (`csv_chunker.py:357+`) **KHÔNG NHẬN** tham số `header_footer_enabled` và cắt
```python
region_lines = lines[region.header_idx : region.last_data_idx + 1]
```
→ **`pre` / `post` bị LOẠI TRỪ VỀ MẶT CẤU TRÚC.**
`chunking/__init__.py:515` gọi nó **KHÔNG kèm flag**.

> **FLAG ĐANG LIVE-TRUE NHƯNG TRƠ.**

### 27.3 Root cause = **FIX-REFIX**: một fix cố ý đã ÂM THẦM regress một feature đã ship

`20260612_0209_chunking_policy_dual_index.py` (archived) **cố ý lật strategy**:
> *"flip … từ `table_csv` trung tính sang `table_dual_index` để doc table/CSV phát ra whole-table group chunk SONG SONG với per-row chunk. **Fix aggregation / 'list-all' / min-max recall miss** (vd 'liệt kê dịch vụ' → đáp án ở rank 21, ngoài top-20)."*

→ Một fix **cố ý cho aggregation recall** đã **âm thầm regress** một feature **đã ship** (table context), **vì logic pre/post KHÔNG BAO GIỜ được port sang strategy mới.**

### 27.4 🔴 TEST TẠO NIỀM TIN GIẢ

`tests/unit/shared/test_chunk_table_csv_header_footer.py` gọi **TRỰC TIẾP** `_chunk_table_csv_with_context(...)` (`:60, :73, :84, :96, :113`) — **không bao giờ đi qua dispatch live với `strategy="table_dual_index"`.**

> **CẢ 6 TEST XANH TRONG KHI PRODUCTION VỨT INTRO/FOOTER.**

### 27.5 🔴🔴 **BONUS — DRIFT PROD vs FRESH DB (nghiêm trọng)**

**KHÔNG CÓ migration ACTIVE nào seed `chunking_policy`.**
Seed chỉ nằm trong archive pre-squash (0208/0209); `alembic/versions/20260618_squash_baseline.py` **KHÔNG mang nó theo**.

→ **DB MỚI dựng từ chain active KHÔNG có row `chunking_policy`** → rơi về `DEFAULT_TABLE_STRATEGY = "table_csv"` (`constants/_11_...:28`)
→ **header/footer CHẠY trên dev, HỎNG trên prod.**

> **Đây CHÍNH XÁC là cách regression này trốn được.**
> **Và nó có nghĩa: dev/CI KHÔNG THỂ TÁI HIỆN table-chunking của prod. Cả hai đang chạy 2 strategy KHÁC NHAU.**

### 27.6 FIX

1. Port `region.pre` / `region.post` vào `_chunk_table_dual_index`, **tái dùng** tham số `header_footer_enabled` + constants đã có (~10 dòng)
2. **Mở rộng test ghim để chạy qua DISPATCH LIVE** → nó không bao giờ được xanh lại khi prod hỏng
3. 🔒 **Thêm alembic ACTIVE seed `chunking_policy`** → DB fresh/clone khớp prod
4. ⚠️ **CẦN RE-INGEST** corpus table hiện có để chunk mới vật chất hóa — **chính migration 0209 đã cảnh báo điều này**

---

## 28. BẢNG TỔNG CUỐI — 29 mục

| # | Mục | VERDICT | Nguồn gốc | Đã từng fix? | Fix ở đâu |
|---|---|---|---|---|---|
| **A1** | Parser registry chết trên prod | ✅ **CONFIRMED — BUG #1** | KẾ THỪA | ⚠️ **fix `de89da8` BỊ VÔ HIỆU bởi chính bug này** | worker + **RE-INGEST** |
| **A2** | `rrf_round_robin` 0 caller | ✅ CONFIRMED | KẾ THỪA | ❌ | ⚠️ **ĐO trước, đừng wire mù** |
| **A3** | `null_embedder` | ✅ file · 🔴 **tác động SAI** | KẾ THỪA | ❌ (có DEAD-CODE NOTICE) | 🗑️ **XÓA** |
| **A4** | `IdempotencyService` 0 caller | 🔴 **REFUTED — grep sai tên** | — | — | ❌ **KHÔNG ĐỘNG VÀO** |
| **A5/A6** | CRAG facade | ✅ **CONFIRMED số liệu chính xác** | KẾ THỪA | 🚨 **FIX HÔM QUA (`5c4fdda`) THẤT BẠI — survivorship bias** | **ĐO LẠI, cấm tune tiếp** |
| **A7** | `neighbor_expand` OFF | ✅ OFF · 🔴 "chưa wire" REFUTED | KẾ THỪA | ❌ | 🧪 **thí nghiệm T1 tốt nhất — SAU A1** |
| **A8** | `critique_parse` OFF | ✅ OFF | QUYẾT ĐỊNH ĐÚNG | ❌ | 🚫 **để nguyên** (cần sysprompt owner, sacred#10) |
| **A9** | `reflect` / `graph_retrieve` | ✅ 0 step | 🔴 **QUYẾT ĐỊNH CÓ SỐ ĐO** | ❌ | 🚫 **TUYỆT ĐỐI KHÔNG BẬT** (3.57s/turn) |
| **B1** | Cliff bỏ qua `min_keep` | ✅ CONFIRMED (18.1%) | KẾ THỪA | ⚠️ **floor 3 lần** | **thứ tự filter** |
| **B2** | Seam 0.45<0.6 | 🟡 PARTIAL | KẾ THỪA | ❌ | contract selector + **telemetry TRƯỚC** |
| **B3** | MMR 0.88 | ✅ runtime · 🔴 constant REFUTED | CÓ CHỦ ĐÍCH | ⚠️ **fix nửa vời** | **ALEMBIC** (2 key) |
| **B4** | `factoid` skip rerank | 🔴 **REFUTED 0/741** | KẾ THỪA | ❌ | ❌ **KHÔNG FIX** |
| **B5** | `grade_timeout` | ✅ đã ship | CÓ CHỦ ĐÍCH | 🚨 **xem A5** | **REVERT/ĐO LẠI** |
| **C1** | VN tokenizer | 🔴 **REFUTED + ĐẢO CHIỀU** | KẾ THỪA | ⚠️ `be94f58` chưa merge | **INGEST** + **REINDEX** |
| **C2** | NFC dense | ✅ CONFIRMED | KẾ THỪA | ❌ | `_embed_query` |
| **C3** | vn_segment gate | ✅ CONFIRMED | KẾ THỪA | ⚠️ fix chưa merge | **XÓA** call query |
| **D1** | HNSW `idx_scan=0` | ✅ sự kiện · 🔴 **root cause REFUTED** | KẾ THỪA | ⚠️ **đã triage `plans/20260709`** | **XÓA COMMENT DỐI** + `iterative_scan` |
| **D2** | Swap cùng dim | 🟡 cơ chế thật · 🔴 **thiệt hại live REFUTED** | KẾ THỪA | ❌ | cột provenance + check trên ROW |
| **D3** | Dim guard | ✅ CONFIRMED cả 3 | KẾ THỪA | ❌ | ingest store + embedder |
| **D4** | Coverage gate mù | ✅ · 🔴 **SAI THỦ PHẠM** (`hdt` không phải `proposition`) | CÓ CHỦ ĐÍCH | ⚠️ **ship→mất→vớt** | strategy boundary |
| **E1** | Cache bỏ qua guard | ✅ **CONFIRMED — LIVE, AN NINH** | KẾ THỪA | ❌ chưa từng | 🚨 **SHIP TRƯỚC** |
| **E2** | XML inject gate NGÀY SINH | ✅ CONFIRMED cả 3 | KẾ THỪA | ❌ | **QUYẾT ĐỊNH**: ADR-0009 |
| **E3** | Grounding fail-closed | 🔴 **REFUTED — vu oan code TỐT** | CÓ CHỦ ĐÍCH | ❌ | **1 ADR, 0 code** |
| **F1a** | Dedup key ngược | ✅ CONFIRMED | KẾ THỪA | ⚠️ **7 patch / 12 ngày — THRASH** | ADR-0008 ext |
| **F1b** | Không provenance | ✅ CONFIRMED | KẾ THỪA | — | **1 cột SELECT** (JOIN đã có!) |
| **F1c** | Cột bị DROP | 🟡 **GÂY HIỂU LẦM** — giàn giáo chưa từng có logic | KẾ THỪA | — | **KHÔNG hồi sinh cột** |
| **F1d** | Test:68 ghim bug | 🔴 **REFUTED** — nó ghim hành vi ĐÚNG | — | — | ❌ **KHÔNG ĐỘNG** |
| **F2** | 2 nhánh vô dụng | ✅ CONFIRMED (2 row, không phải 3) | CÓ CHỦ ĐÍCH | ❌ | xóa 2 coroutine |
| **F3** | condense/router chết | ✅ CONFIRMED live | CÓ CHỦ ĐÍCH | ❌ | xóa node (**giữ `_router_route`**) |
| **F4** | Atomic protect OFF | ✅ flag · ⚠️ **hậu quả CHƯA VERIFY** | KẾ THỪA | ❌ | **failing test TRƯỚC** |
| **F5** | Intro/footer bảng | ✅ · 🔴 **cơ chế SAI** — flag TRƠ | CÓ CHỦ ĐÍCH | ⚠️ **fix aggregation ĐÃ regress cái này** | port + **alembic seed** + RE-INGEST |

### Điểm số cuối

```
🔴 BỊ BÁC HOÀN TOÀN          :  B4 · C1(đảo chiều) · A4 · E3 · F1d · D1-root-cause   =  6
🟡 CONFIRMED nhưng SAI bản chất/thủ phạm :  B2 · B3 · D2 · D4 · F1c · F5             =  6
✅ CONFIRMED + fix đúng như mô tả        :  A1 · A5/A6 · B1 · C2 · C3 · D3 · E1 · E2
                                            F1a · F1b · F2 · F3 · F4 · A2/A3/A7/A8/A9 = 17
```

> **Nếu ship thẳng theo audit cũ: 12/29 mục sẽ là FIX BẨN** — trong đó **3 mục gây HẠI THẬT**
> (C1 → giảm recall · A4 → phá idempotency BE-to-BE · A9 → tái tạo regression 3.57s/turn).

---

## 29. 📌 4 BÀI HỌC PHƯƠNG PHÁP — GHI VÀO QUY TRÌNH

### 29.1 CẤM đọc hằng số rồi suy ra runtime
B4 (57.7% → thật là **0.0%**) sinh ra vì đọc `frozenset` mà bỏ qua vế `and _size_safety`.
→ **Số liệu hành vi PHẢI đến từ `request_steps` / log, KHÔNG từ việc đọc code.**

### 29.2 CẤM grep theo TÊN THUỘC TÍNH đoán mò
A4 ("0 caller" trên hạ tầng chịu tải) sinh ra vì grep `_idempotency` trong khi tên thật là `_idem`.
→ **Grep theo TÊN METHOD / TÊN SYMBOL.**

### 29.3 "0 step runtime" ≠ "chưa wire"
`neighbor_expand` và `critique_parse` **NẰM TRÊN EDGE VÔ ĐIỀU KIỆN và CHẠY MỖI QUERY** — chúng không phát step **vì enable-gate nằm TRƯỚC span**.
→ **Phải kiểm tra span nằm TRƯỚC hay SAU gate.**

### 29.4 CẤM tính p95 trên mẫu bị cắt cụt (survivorship bias)
Fix `5c4fdda` thất bại vì "p95 = 2.56s" tính **chỉ trên các lần grade THẮNG cap 2.0s cũ**. 306 lần timeout là **dữ liệu bị kiểm duyệt phải**.
→ **Muốn biết latency thật: đo KHÔNG timeout.**

---

## 30. THỨ TỰ SHIP (ràng buộc phụ thuộc)

```
┌─ ĐỢT 0 — CHẶN, LÀM NGAY ────────────────────────────────────────────┐
│ 0.1  🚨 E1  cache hit → guard_output      (lỗ hổng an ninh LIVE)     │
│ 0.2  🚨 A5  REVERT/xử lý grade timeout    (fix hôm qua = REGRESSION) │
│ 0.3  🔧 X2  sửa test đỏ tại HEAD          (không ship lên nền đỏ)    │
└─────────────────────────────────────────────────────────────────────┘
           │  E1/A5 độc lập với chunking → làm song song được
           ▼
┌─ ĐỢT 1 — A1 (ĐỔI CORPUS → VÔ HIỆU MỌI PHÉP ĐO TRƯỚC NÓ) ───────────┐
│ 1.1  A1  worker truyền raw_bytes → parser registry sống lại         │
│ 1.2  F5  port pre/post vào table_dual_index + alembic seed          │
│ 1.3  C1  gỡ segmentation ingest                                     │
│ 1.4  ⚠️ RE-INGEST + REINDEX  ← 1.1/1.2/1.3 ĐỀU cần → GỘP 1 LẦN      │
│ 1.5  ĐO LẠI BASELINE (mọi số trước đợt này đã CHẾT)                 │
└─────────────────────────────────────────────────────────────────────┘
           ▼
┌─ ĐỢT 2 — QUERY-SIDE (an toàn, không cần re-ingest) ─────────────────┐
│ 2.1  B1  cliff back-fill min_keep     (tái dùng pattern mmr_filter)  │
│ 2.2  B3  alembic MMR 0.88→0.98 (CẢ global + by_intent.factoid)      │
│ 2.3  C2  NFC ở _embed_query                                         │
│ 2.4  C3  xóa segment_vi_compounds query-side                        │
│ 2.5  F1  provenance (1 cột SELECT) + conflict event + bỏ score 1.0  │
│      ⚠️ 1 FIX = 1 LẦN ĐO. KHÔNG GỘP.                                │
└─────────────────────────────────────────────────────────────────────┘
           ▼
┌─ ĐỢT 3 — DỌN RÁC + LAN CAN (không đổi hành vi) ────────────────────┐
│ 3.1  D1  XÓA 3 comment nói dối + SET hnsw.iterative_scan            │
│ 3.2  D3  dim-guard per-vector (sau khi audit ai_models.dimension)   │
│ 3.3  D2  cột embedding_model + check trên ROW                       │
│ 3.4  F2/F3  xóa node + nhánh chết (giữ _router_route!)              │
│ 3.5  A3  xóa null_embedder                                          │
│ 3.6  D4  sửa locator coverage gate                                  │
└─────────────────────────────────────────────────────────────────────┘
           ▼
┌─ ĐỢT 4 — QUYẾT ĐỊNH (0 code hoặc cần A/B) ─────────────────────────┐
│ 4.1  E3  1 ADR cho cả họ app-override      (0 code, ~1-2h)          │
│ 4.2  E2  ADR-0009 + GIẾT gate-theo-ngày    (cần A/B, 4 bot live)    │
│ 4.3  B2  telemetry chunk_strategy TRƯỚC → rồi mới fix seam          │
│ 4.4  A7  A/B neighbor_expand (chỉ SAU khi A1 xong)                  │
│ 4.5  A2  đo comparison-intent → wire HOẶC XÓA                       │
│ 4.6  F4  failing test TRƯỚC → rồi mới flip atomic protect           │
└─────────────────────────────────────────────────────────────────────┘

🚫 KHÔNG BAO GIỜ:  A9 reflect (3.57s/turn) · A9 graph_retrieve · A8 critique_parse
                   B4 rerank skip-list · A4 IdempotencyService · F1d test
                   khôi phục 4 cột superseded_by (giàn giáo, chưa từng có logic)
```

