# Eval flow — 4 stage (tách CHẤM khỏi CHẠY, agent-judge thay rule-scorer)

Lý do: rule-scorer (substring/overlap) chấm SAI paraphrase VN (vd "ít nhất hai"≈"tối thiểu hai" → false-fail). Tách scoring sang agent-judge độc lập.

## Stage A — COLLECT (no score)
`python scripts/eval_collect.py <golden> <bot_id> <ch> <ws> [workers=1]`
→ chạy câu hỏi → dump `reports/EVAL_COLLECT_<bot>.json` = `[{cat,q,expected,bot_answer,chunks_used,top_score,error}]`. KHÔNG pass/fail. workers=1 mặc định (tránh innocom 503 concurrency).

## Stage B — AGENT JUDGE (chấm chuẩn)
Workflow `eval-agent-judge` → 1 agent/bot đọc collected → chấm NGỮ NGHĨA (PASS cho paraphrase/synonym, FAIL strict số/giá/SKU, PROVIDER_ERROR loại khỏi mẫu) → `reports/EVAL_AGENT_JUDGE.md` + true_pct + scorer_false_negatives. KHÔNG chấm lúc chạy.

## Stage C — REPLAY câu sai (consistency)
`python scripts/eval_replay_debug.py <bot_id> <ch> <ws> <N> "<câu sai 1>" ...`
→ hỏi lại mỗi câu N lần → phân biệt **SAI-BỀN** (luôn sai cùng step) vs **FLAKY(provider)** (innocom 503 lúc được lúc không). dist step mỗi lần.

## Stage D — DEBUG step (cho câu SAI-BỀN)
Từ replay: step nào hỏng (NO-RETRIEVE / wrong-chunk / REFUSE / ANSWERED-wrong / PROVIDER). Trace gốc: chunk có ingest? top_score? entity attribute đúng? → fix đúng tầng (data/ingest/retrieval/sysprompt).

## Nguyên tắc
- CHẤM ≠ CHẠY: collect xong mới judge (agent), không inline rule-score.
- Loại PROVIDER_ERROR (innocom 503) khỏi mẫu — không tính là lỗi đáp.
- Câu sai → replay N lần trước khi kết luận; chỉ SAI-BỀN mới debug step.
