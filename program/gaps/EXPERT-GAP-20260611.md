# EXPERT-GAP — phát hiện từ load test + DB-verify sâu (2026-06-11)

> Nguồn: `reports/QA_FORMAT_REPORT_20260610.json` (verify đáp án↔DB per-fact),
> `reports/PROJECT_REPORT_20260610.md` §8, đo trực tiếp ZE rerank + reproduce live.
> Bối cảnh: sau khi diệt 429/503 (5 fix reranker), đào sâu chất lượng RAG → lộ 4 gap
> mà con số "87% pass / HALLU=0" từ 1-run che mất. Đây là rào chắn "tốt → expert".

## Sự thật đã verify (evidence-driven)

| # | Gap | Evidence | Tầng |
|---|---|---|---|
| G1 | **HALLU intermittent** — bot bịa số khi retrieval thin | `thong-tu/Q1` run-23:12 bịa "16/2016" (0 chunk corpus, đo DB); 6 re-run sau đó đúng 18/2018 | sysprompt anti-fabricate + retrieval |
| G2 | **First-stage recall FLAKY** | chunk đáp án rerank #2 (0.69-0.82, đo trực tiếp) nhưng KHÔNG vào candidate set cho rerank ở 1 số run | retrieval (embedding/BM25/multi-query) |
| G3 | **Coverage thiếu trên câu nhiều phần** | ~10 câu judge=CHUẨN nhưng bot bỏ sót factoid (lich-su/Q8 thiếu 30-4-1975+Dương Văn Minh; spa/Q8 thiếu giá 11.999.000) | sysprompt + generation |
| G4 | **Eval che lỗi** | LLM-judge chấm "retrieval miss", KHÔNG cross-check số bịa vs corpus → fabrication lọt; 1-run pass-rate không phát hiện intermittent | harness eval |

## Vì sao "chưa expert"
Quality THẬT dao động giữa các run (non-deterministic: multi-query LLM variants +
speculative retrieve). Không thể bảo đảm **HALLU=0 across runs**. Con số đẹp từ 1-run
KHÔNG đáng tin. Đây là khác biệt cốt lõi giữa "demo tốt" và "production expert".

## Số liệu 2 run (cùng bộ 120 câu, cùng config slow-mode)
| Run | DB-đúng | nghi-bịa |
|---|---|---|
| multistep 23:12 | 96/120 (80%) | **1 (thong-tu 16/2016)** |
| detailed 00:24 | 91/120 (75.8%) | **0** |
→ chênh lệch run-to-run = bằng chứng non-determinism.

## Roadmap fix (đúng tầng, ưu tiên T1)

### F1 — Recall determinism [T1, leverage cao nhất]
- **Root**: chunk đáp án có literal đặc trưng ("18/2018/TT-NHNN", "ô tô", "30.000.000")
  nhưng đôi khi không vào top-K candidate. Embedding/multi-query non-deterministic.
- **Fix candidate** (cần đo trước khi ship):
  (a) đảm bảo **BM25/keyword path** luôn fire + merge (literal terms → recall ổn định),
  (b) luôn include **query gốc** trong retrieval set (không phụ thuộc hoàn toàn LLM variant),
  (c) tăng candidate top-K trước rerank (rerank đã tốt — score #2 — chỉ cần feed đủ).
- **Verify**: N-run determinism test, chunk đáp án phải vào candidate ≥ N/N lần.

### F2 — Anti-fabricate defense-in-depth [T1, sacred]
- Khi retrieval thin → bot phải **REFUSE**, KHÔNG bịa số/mã. Sysprompt anti-fake hiện có
  nhưng GIỮ không vững (intermittent). Siết per-bot qua **alembic** (KHÔNG psql hotfix).
- **Verify**: trap câu out-of-corpus + câu retrieval-miss → 0 fabrication trên N run.

### F3 — Coverage câu nhiều phần [T1]
- Sysprompt per-bot ép trả lời **ĐỦ MỌI PHẦN** câu hỏi (liệt kê từng ý). Per-bot, alembic.
- **Verify**: 10 câu factoid-miss (lich-su, dia-ly, spa giá) → DB-đúng tăng.

### F4 — Eval expert [harness, gate]
- Judge phải **cross-check mọi số/mã/tên vs corpus** (bắt fabrication) — `build_detailed_qa
  _report.py` đã là prototype (suspected_fabrication + in_corpus).
- Chạy **N=3 run/câu**, flag bất kỳ flip/fabrication. Pass-rate 1-run = KHÔNG đủ gate.

## Nguyên tắc (CLAUDE.md)
- Mọi fix sysprompt qua **alembic tracked**, KHÔNG psql UPDATE `bots.system_prompt`.
- KHÔNG app-inject text vào LLM; anti-fabricate/coverage là rule trong `system_prompt` của owner.
- KHÔNG hard-code per-bot logic trong core; khác biệt qua config DB.
- Đo trước, claim sau — N-run, không tin 1-run.
