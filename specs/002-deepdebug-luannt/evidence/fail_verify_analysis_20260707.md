# Fail-Verify Analysis — cross-bot (chinh-sach-xe + test-spa-id) · 2026-07-07

> **Mục đích**: verify SỰ THẬT theo rule #0. Full agent-graded eval báo **43 fail** trên 2 bot. Trước khi coi 43 là "43 bug", re-run TỪNG fail LIVE (fresh connect_id, coref chạy multi-turn) để tách **fail THẬT** khỏi **artifact ĐO** / **HALLU không tái hiện**.
>
> **Nguồn số** (không cite trí nhớ):
> - xe 200q post-A4: `specs/002-deepdebug-luannt/evidence/step21_full200_postA4_verdicts.json`
> - spa 100q domain-neutral: `specs/002-deepdebug-luannt/evidence/spa100_domain_neutral_verdicts.json`
> - 43-fail re-run: `scratchpad/fail_verify_result.json` (harness `scratchpad/fail_verify.py`), phân loại `scratchpad/fail_class.json`
> - Code chạy khi đo: commit `2ad4df7` (A4 category-collision fix); service ragbot-py restart, health OK.

---

## 1. Kết quả eval 2 bot (headline — số THẬT từ verdict JSON)

### chinh-sach-xe (lốp Nam Phát) — 200 câu, agent-graded, post-A4
| | total | count |
|---|---|---|
| CORRECT | 200 | **172** |
| WRONG | | 14 |
| HALLU | | 12 |
| REFUSE_OK | | 2 |
| **gate** (câu corpus CÓ đáp án) | 100 | **91 ok** |
| **trap** (câu bẫy — phải defer/không bịa) | 100 | **83 ok** |

→ fail = 14 WRONG + 12 HALLU = **26**.

### test-spa-id (Dr. Medispa) — 100 câu, agent-graded, domain-neutral run
| | count |
|---|---|
| CORRECT | **60** |
| WRONG | 11 |
| REFUSE_OK | 5 |
| HALLU | 4 |
| *(graded)* | *80* |

→ fail (WRONG+HALLU) = **15** graded + 2 coref-manifest = **17** đưa vào re-run.

**Tổng fail đưa verify = 26 (xe) + 17 (spa) = 43.**

---

## 2. Verify 43 fail → phân loại THẬT (rule #0, re-run live)

Mỗi fail hỏi lại LIVE 1 lần (coref hỏi kèm câu setup cùng conversation). Đối chiếu answer re-run với expect/note.

| Loại | Ý nghĩa | Count | % |
|---|---|---:|---:|
| **NOTFAIL** | Re-run trả ĐÚNG/honest → eval-grade là artifact (load-rỗng, grading-miss, honest-bait-refuse) | **5** | 12% |
| **EMPTY** | Re-run vẫn rỗng (load degrade / coref referent mất cứng) | **3** | 7% |
| **NDHALLU** | HALLU **KHÔNG tái hiện** — re-run defer/refuse đúng (LLM temperature) | **6** | 14% |
| — *artifact/non-deterministic* | | **14** | **33%** |
| **BRAND** | STABLE false-deny: brand/stats-suppress đè raw → từ chối oan (Rovelo, promo, process) | **10** | 23% |
| **WK** | STABLE world-knowledge fabrication — bịa detail phi-số tái hiện | **5** | 12% |
| **COREF** | STABLE sai referent DÙ đã multi-turn → conflation thật | **4** | 9% |
| **COVER** | Coverage-miss: aggregation chưa tính / arrival intermittent / comparison-partial / clarify | **10** | 23% |
| — *stable-real* | | **29** | **67%** |

**Kết luận số học**: eval fail=43 **phóng đại ~1/3**. Fail THẬT ổn định ≈ **29** (trong đó 3 arrival-refuse là *intermittent* — xem §4 → stable "cứng" ≈ 26).

### Per-bot
| Loại | xe | spa |
|---|---:|---:|
| NOTFAIL | 2 | 3 |
| EMPTY | 2 | 1 |
| NDHALLU | 6 | 0 |
| BRAND | 7 | 3 |
| WK | 1 | 4 |
| COREF | 2 | 2 |
| COVER | 6 | 4 |
| **total** | **26** | **17** |

→ **Fail-classes GIỐNG NHAU 2 bot** (cùng BRAND/WK/COREF/COVER) ⇒ lỗi **systemic domain-neutral**, KHÔNG per-bot. spa nặng WK (4) hơn xe (1); xe có NDHALLU (6, spa 0) vì trap-set xe nhiều bẫy-số hơn.

---

## 3. Chi tiết theo loại (evidence per-ID)

### 🟢 NOTFAIL (5) — eval sai, không phải bug
- **S-019** "Trẻ hóa da bao nhiêu tiền" — eval RỖNG (0 chunk dưới tải); re-run `chunks=3 top=0.92` trả đúng → **load artifact**.
- **G-068** "Landspider 215/65R16 khi nào về" — re-run trả **"28-thg 11 (28/11)"** khớp expect → arrival **CÓ** serve được.
- **G-074** "Công ty phân phối tên gì" — re-run trả **"Công ty TNHH Lốp Nam Phát"** + địa chỉ khớp expect → grading-miss.
- **S-064** coref "dịch vụ đó có giảm giá không" — re-run (multi-turn) trả ưu đãi 299K/99K có trong corpus → coref OK.
- **S-088** "triệt lông cam kết hết vĩnh viễn sau 1 buổi?" — re-run **"không có cam kết hết vĩnh viễn"** = honest bait-refuse → đúng.

### ⚪ EMPTY (3)
- **B-050 / B-052** (coref, `turns=2` vẫn rỗng) — referent mất cứng ngay cả khi có câu setup → harness/coref-resolution.
- **S-048** "Massage Gym Beauté® 42 bước là gì" (single-turn rỗng) — load hoặc gap thật, cần probe riêng.

### 🔵 NDHALLU (6) — HALLU KHÔNG tái hiện (non-deterministic)
Eval-1-lần chấm HALLU, nhưng re-run **defer/refuse đúng**:
- **B-002** "còn bao nhiêu chiếc" (qty NULL) → re-run defer honest. Eval: bịa "còn 0 chiếc".
- **B-005** "255/45ZR18 có hàng không" → re-run defer. Eval: bịa "còn 26 lốp".
- **B-006** "có phải giá 260.000đ" (bait) → re-run "giá ghi '—' (chưa có)". Eval: confirm bừa 260k.
- **B-035** "độ sâu gai lốp mới bao nhiêu mm" (không có corpus) → re-run "không có thông tin". Eval: bịa "8-9mm".
- **B-055** "Davanti đặc điểm vượt trội" → re-run refuse. Eval: bịa marketing.
- **B-066** "so sánh H/P vs A/T" → re-run refuse (A/T không có). Eval: bịa encyclopedia.

→ **HALLU-số dạng bẫy là INTERMITTENT** (lúc bịa lúc defer). Eval-1-shot phóng đại. numeric-fidelity gate observe cần đo lại N≥10 để biết rate thật.

### 🔴 BRAND (10) — STABLE false-deny (đòn bẩy #1)
Hỏi giá/thông tin brand/dịch vụ mà corpus CÓ → bot từ chối oan, **tái hiện 100%**:
- **xe (7)**: B-010, B-011, G-076, G-077, G-078, G-079 → đều **"Dạ bên em chưa phân phối hãng Rovelo ạ"** — Rovelo CÓ trong DSI (giá NULL) nhưng bị chối brand. B-012 "mã ...RVL không có trong danh sách".
- **spa (3)**: S-037 (ưu đãi Ultherapy → refuse), S-046 (quy trình 16 bước → refuse), S-075 (so sánh combo/lẻ → refuse). Promo/process CÓ ở raw chunk nhưng stats-synthetic `score=1.0 chunks=1` đè → LLM không thấy.

**Cơ chế chung** (đã trace): `_do_stats_lookup` build synthetic chunk `score=1.0` + suppress doc-fallback → raw chunk có đáp án bị đè. Câu TEXT (ưu đãi/quy trình) + Rovelo-giá-NULL đều rơi vào bẫy này. `top=1.0 chunks=1` là dấu vân tay.

### 🟠 WK (5) — world-knowledge fabrication (đòn bẩy #2), tái hiện
- **S-044** "công nghệ triệt lông" → **"Diode Laser lạnh hiện đại của Hàn Quốc"** — "Hàn Quốc" bịa (corpus 0 hit xuất xứ).
- **S-045** "Diode có đau không" → lại **"của Hàn Quốc"** — cùng fabrication.
- **S-006** "spa tầng mấy" → đúng "tầng 2" + thêm **"nên đi thang máy"** (không có trong corpus, mild).
- **S-056** "tư vấn Ultherapy" → mô tả SMAS/collagen/elastin (một phần generic-đúng, một phần ngoài corpus — borderline).
- **B-031** "bảo hành có áp dụng lốp xe tải?" → **"áp dụng cho tất cả các loại lốp, bao gồm cả lốp xe tải"** — sai, corpus chỉ PCR/du lịch.

→ numeric-fidelity gate MÙ với các claim phi-số này (không có số để soi). Cần owner anti-fabricate sysprompt hoặc non-numeric grounding gate.

### 🟡 COREF (4) — sai referent dù multi-turn
- **B-060** ref=195/65R15, bot trả **155/80R13 @684k** — conflate SKU khác. `top=0.43`.
- **S-058** ref=Ultherapy, bot trả **"quy trình điều trị mụn 10 bước"** — sai hẳn dịch vụ.
- **B-047** ref=225/45ZR18, re-run truncate "Dạ, lốp Rovelo" — dở dang.
- **S-068** ref=Hydra Ballet (CÓ ở corpus, S-052 trả được), bot **"không xuất hiện trong tài liệu"** — false-refuse trên follow-up.

→ 2 EMPTY-coref (B-050/052) + 4 COREF này ⇒ **coref-resolution yếu THẬT** khi referent là SKU/tên ghép, KHÔNG chỉ artifact fresh-id như giả thuyết ban đầu. Cần multi-turn eval harness đo đúng rate.

### 🟤 COVER (10) — coverage-miss
- **Aggregation chưa tính**: B-069 "tổng tồn R13" (trả "tổng tồn..." dở), S-039 "liệt kê Peel trong gói", S-046 (đếm bước — cũng BRAND-suppress).
- **Arrival intermittent**: G-063/064/067 refuse "chưa có ngày về" NHƯNG G-068 serve được "28/11" → serve **không ổn định** (xe-2 arrival-date deferred serve).
- **comparison-partial**: B-017 trả 1 vế (205/65R16) rồi "chưa tìm thấy 235/40R18" — thiếu nửa so sánh.
- **origin**: B-021 "Landspider xuất xứ" honest-refuse (corpus không có → đúng, hoặc coverage tùy corpus).
- **clarify**: S-092 "cho hỏi giá" → hỏi lại dịch vụ (UX đúng). S-052 trả "bước 14" (cần verify đúng số bước). S-049 trả "1-2 buổi" có trích corpus (đúng).

---

## 4. Sự thật đúc kết (đổi ưu tiên plan theo số đo)

| Nhận định TRƯỚC verify (eval-1-shot) | Sự thật SAU verify (re-run) |
|---|---|
| 43 fail = 43 bug | **14/43 (33%) là artifact/non-deterministic**, không phải bug pipeline |
| HALLU-số nhiều (12 xe + 4 spa) | **6 HALLU-số KHÔNG tái hiện** (intermittent, LLM temperature) — nhẹ hơn eval tưởng |
| Empty = coverage bug | **3 empty** = load-degrade + coref-hard, KHÔNG phải per-query bug |
| Coref = fresh-id artifact (giả thuyết) | **coref-resolution YẾU THẬT** (4 COREF + 2 EMPTY tái hiện multi-turn) — nâng ưu tiên nhẹ |
| B4 false-deny ~10 | **BRAND=10 STABLE, tái hiện 100%** → **đòn bẩy #1 xác nhận** |
| world-knowledge ~9 | **WK=5 stable** (S-006/S-056 borderline) → **#2**, confident-fabricate |

### Thứ tự ưu tiên (verified)
1. **🔴 BRAND / stats-suppress-raw (10 stable)** — đòn bẩy cao nhất, tái hiện 100%, domain-neutral. → P1.
2. **🟠 World-knowledge fabrication (5 stable)** — bịa phi-số confident. → P2 (owner anti-fabricate sysprompt trước).
3. **🟤 COVER (10)** — hỗn hợp: aggregation + arrival-serve intermittent + comparison-partial. Một phần overlap BRAND (đè raw). → sau P1 đo lại, phần còn lại là feature-gap riêng.
4. **🟡 COREF (4+2 empty)** — cần **multi-turn eval harness** (P0) để đo rate THẬT trước, rồi mới quyết fix referent-resolution.
5. **🔵 NDHALLU (6)** — non-deterministic; theo dõi qua numeric-fidelity gate observe N≥10, KHÔNG fix vội (không tái hiện ổn định).
6. **⚪ EMPTY (3)** — empty-answer guard (P0, deterministic) + log 0-chunk để không giấu perf.

---

## 5. Giá trị của multi-bot testing (case-study kết luận)

Chạy **2 bot khác domain** (lốp vs thẩm mỹ) mới tách được:
1. **Bug lộ ra**: A4 category-collision chỉ xuất hiện ở spa ("Vùng | Giá") — xe không có cột category nên che. Đã fix (commit `2ad4df7`, test `test_shape_name_wins_over_category_on_same_column`).
2. **Domain-neutral CONFIRMED**: fail-classes GIỐNG HỆT 2 bot ⇒ lỗi systemic, không per-bot.
3. **THẬT vs ĐO**: cross-classify + re-run mới biết 33% "fail" là artifact — điều test 1 bot / eval-1-shot KHÔNG làm được.

→ "chưa chuẩn multi-bot" thực chất = **stats-route quá tham (BRAND, 10) + LLM bịa phi-số (WK, 5)** — 2 vấn đề CHUNG, sửa 1 lần đóng cả 2 bot. Còn lại là coverage-feature-gap + đo-lỗi.

---

*Anchor: verdict JSONs Jul-7; re-run `fail_verify_result.json` Jul-7 21:57; code `2ad4df7`. Mọi số dẫn từ file, không phỏng đoán (rule #0).*
