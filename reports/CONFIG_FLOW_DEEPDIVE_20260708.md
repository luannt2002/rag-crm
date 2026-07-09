# Deep-dive: luồng CONFIG end-to-end + vì sao "key nhìn như chết mà không chết"

> **Nguồn**: đọc code thật (`file:line` dẫn bên dưới) + đo trên DB dev 2026-07-08.
> **Bối cảnh**: anh hỏi "175 key có cái nào không dùng, bỏ được không?". Câu trả lời
> đúng cần hiểu **một value đi qua bao nhiêu tầng** — vì 4 lớp gián tiếp làm grep tĩnh
> ra 3 list SAI liên tiếp (đã tự bắt, rule#0). Đây là bản đồ luồng để không bị lừa nữa.

---

## 0. TL;DR (sự thật đo được)

- **172/175 key contract đang SỐNG thật** (sau khi bỏ 3 key chết). Surface config **KHÔNG phình rác** — chỉ bị *che khuất* bởi 4 kiểu đọc gián tiếp.
- **3 key CHẾT thật** (load→build vào `pipeline_config` nhưng KHÔNG consumer nào đọc): `pipeline_grade_chunk_preview`→`grade_chunk_preview`, `default_answer_autonomy_percent`, `short_query_word_threshold` → **đã comment** (cả tuple + builder, 3 file). Chúng vẫn còn **3 dead seed row** trong `system_config` → DATABASE team xóa qua alembic.
- **71/175 key chưa seed** → rơi về hằng số `DEFAULT_*` (ownership drift — xem `CONFIG_COMPLETENESS_GATE_20260708.md`).
- **`rag_top_k`/`rag_rerank_top_n` KHÔNG chết** — chúng cấp `system_default=` cho `resolve_bot_limit("retrieval_top_k"/"rerank_top_n")`. Chỉ là **tên khác runtime** (drift, `audit_config_key_drift.py` đã cảnh báo) — reconcile tên, KHÔNG xóa.
- **Kết luận phương pháp**: muốn danh sách dead ĐẦY ĐỦ & đáng tin → phải **trace runtime** (log key thực đọc khi chạy load-test), KHÔNG grep tĩnh. Đúng chuẩn dự án "VERIFIED = số thật runtime".

---

## 1. Luồng một value đi từ DB tới hành vi node

```
┌─ system_config (jsonb) ── plan_limits ── bots.<col> ── threshold_overrides   [DB, nguồn sự thật]
│        │  Redis-cache (cfg_svc)
│        ▼
│  get_many(_PIPELINE_CFG_KEYS)  ── 1 round-trip, batch ~172 key
│        _pipeline_config.py:375  →  dict `raw` (key thiếu row = KHÔNG có trong raw)
│        ▼
│  _build_pipeline_config(raw)   ── builder: đổi tên + coerce kiểu + gộp
│        _pipeline_config.py:372  →  state["pipeline_config"]  (dict target-key)
│        ▼
│  NODE đọc value theo 1 trong 4 PATTERN dưới ── quyết định hành vi user thấy
└────────────────────────────────────────────────────────────────────────────
```

Điểm mấu chốt: **tên system_config-key (raw) ≠ tên state-key (target)**. Builder là chỗ đổi tên. Vì vậy tìm "ai dùng key X" phải theo được X (raw) → T (target) → consumer(T).

---

## 2. BỐN pattern đọc config — vì sao grep tĩnh sai

| # | Pattern | Ví dụ (`file:line`) | Grep thường bắt được? |
|---|---|---|---|
| **1** | `_pcfg(state, "T", DEFAULT)` đọc trực tiếp | `query_graph_helpers.py:164` def; vd `_pcfg(state,"grounding_intents",…)` | ✅ dễ |
| **2** | **Builder remap**: `"T": coerce(raw.get("SRC"))` — key system_config `SRC` đổi tên thành `T` rồi node đọc `T` | `_pipeline_config.py:393` `"condense_history_limit": …raw.get("pipeline_condense_history_limit")` | ❌ grep `SRC` ra 0 vì consumer đọc `T` |
| **3** | **`resolve_bot_limit` system_default**: key `SRC` chỉ để cấp default cho per-bot limit `L` | `bot_limits.py:397` def; `_pipeline_config.py:380` `system_default=…raw.get("rag_top_k")` cho `retrieval_top_k` | ❌ grep `rag_top_k` ra 0 chỗ "đọc" |
| **4** | **Dict-access + LangGraph config**: `pipeline_config["T"]` truyền thẳng vào graph | `chat_routes.py:473` `config={"recursion_limit": pipeline_config["graph_recursion_limit"]}` | ❌ `_pcfg`-scan miss (không phải `_pcfg`) |

→ **Một scan chỉ bắt pattern 1 sẽ báo pattern 2/3/4 là "chết" — sai.** Đây chính xác là 3 lần em ra list sai:
- Lần 1 (grep `_pcfg` + literal, **loại builder file**): báo `pipeline_condense_history_limit` chết → SAI (pattern 2, target `condense_history_limit` đọc `_pcfg`=3).
- Lần 2 (AST map builder): parser fail "sources=0" → list 27 SAI.
- Lần 3 (`_pcfg`-only target): báo `graph_recursion_limit` chết → SAI (pattern 4, LangGraph `recursion_limit`).

Chỉ khi trace đủ 4 pattern + đọc tay mới ra 3 key chết THẬT.

---

## 3. Cách duy nhất đáng tin để liệt kê dead ĐẦY ĐỦ — runtime trace

Grep tĩnh không đủ vì pattern 2/3/4 + dynamic access. Đề xuất (chưa chạy — cần approve):

1. Instrument `_pcfg` + wrapper cho `pipeline_config[...]` access + `resolve_bot_limit` → ghi mỗi key **thực sự đọc** vào 1 set (structlog event `cfg_read` hoặc in-memory).
2. Chạy load-test đại diện (gate100 + spa100, đã có) để phủ mọi intent/flow.
3. `read_set` = mọi key runtime chạm. `dead = contract(172) − read_set`.
4. Đây là "số thật" → mới đủ tin để comment/xóa hàng loạt.

Cho tới khi chạy: **chỉ 3 key đã verify tay là an toàn để tắt.** Không tắt thêm dựa trên grep.

---

## 4. Hai loại "rác config" tách bạch (đừng lẫn)

| Loại | Là gì | Chỗ sửa | Trạng thái |
|---|---|---|---|
| **Dead contract key** | key trong `_PIPELINE_CFG_KEYS` nhưng target không ai đọc | comment/xóa khỏi tuple+builder (code — DEV) | 3 key đã comment 2026-07-08 |
| **Dead seed row** | row trong `system_config` mà contract không còn load | alembic DELETE (DB — DATABASE team) | 3 row (`grade_chunk_preview`… ) chờ xóa + rà thêm 264−172 |
| **Unseeded contract key** | contract load nhưng seed thiếu → rơi constant | seed value (DATABASE) hoặc reclassify (DEV) | 71 key, gate baseline |
| **Naming drift** | 2 tên cho 1 concept (`rag_top_k` vs `top_k`/`retrieval_top_k`) | reconcile 1 tên canonical | `rag_top_k`,`rag_rerank_top_n` — `audit_config_key_drift.py` |

→ Contract có 172 key, seed có 264 row → **~92 seed row KHÔNG được contract load** (264−172). Một phần là key đọc ngoài pipeline (callback/ingest/RLS/embedding), một phần là dead-seed-row thật. Rà đầy đủ = chạy runtime-trace §3 rồi diff với toàn bộ 264.

---

## 5. Liên hệ các luồng khác (đã có report riêng)

Luồng config ở trên là **1 trục**. Các luồng runtime khác đã phân tích trong phiên này:
- **Pipeline step/luồng gọi** (ingest U0–U7 + query ~21 node, step nào core/optimize/async-able): `reports/PIPELINE_STEPS_NECESSITY_20260708.md` + `reports/DEEP_ANALYSIS_ALL_FLOWS_20260708.md`.
- **Flag on/off trade-off** (83 flag): `reports/FLAG_TRADEOFF_ANALYSIS_20260708.md`.
- **Correctness + perf** (200q agent-graded): `reports/LOADTEST_RESULT_20260708.md`.
- **Ownership 3-mode** (dev/devops/database): `README_DEV.md` / `README_DEVOPS.md` / `README_DATABASE.md`.

Muốn deep-dive tiếp 1 luồng cụ thể (ingest, retrieve→rerank→grade, RLS, worker exactly-once) → nói tên luồng, em trace `file:line` từng bước.

---

*Mọi anchor dẫn code thật. Danh sách dead đầy-đủ CHỜ runtime-trace (§3) — hiện chỉ 3 key verify-tay đã tắt. Không grep-đoán.*
