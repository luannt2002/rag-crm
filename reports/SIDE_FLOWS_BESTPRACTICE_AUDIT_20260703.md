# SIDE-FLOWS BEST-PRACTICE AUDIT — 4 nhánh phụ (streaming/multi-turn/action/webhook)

> Workflow 4 agent Opus (401k tokens). Mọi claim `file:line`. Audit-only, đề xuất best-practice.

## Verdict: cả 4 nhánh PARTIAL, 9 issue HIGH

| Nhánh | Verdict | HIGH | Tổng |
|---|---|---|---|
| Streaming/SSE | ⚠️ PARTIAL | 2 | 6 |
| Conversation-state/multi-turn | ⚠️ PARTIAL | 1 | 6 |
| **Action/slot-filling (booking)** | ⚠️ PARTIAL | **3** | 6 |
| Webhook/callback | ⚠️ PARTIAL | 3 | 6 |

---

## 1. STREAMING / SSE (⚠️ PARTIAL, 2 HIGH)
- **[H]** KHÔNG có heartbeat/keepalive — `DEFAULT_SSE_HEARTBEAT_MS=15000` định nghĩa nhưng **không dùng đâu**. First-token chậm → proxy/LB/EventSource drop connection. → wrap drain bằng `asyncio.wait_for(sink.get(), timeout=heartbeat)` → yield `: ping`.
- **[H]** Lỗi pipeline KHÔNG gửi về client như SSE error frame — crash → client thấy `done` answer="" (không phân biệt được crash vs refuse). → yield `event: error` frame trước `done`.
- **[M]** Không detect client-disconnect chủ động → lãng phí LLM cost 30s sau khi client bỏ. → poll `request.is_disconnected()` + cancel graph_task.
- **[M]** `SPECULATIVE_REDO_SENTINEL` bị nuốt ở node, `redo_event()` không bao giờ fire (dead wire helper). → forward control event hoặc gỡ helper dead.

## 2. CONVERSATION-STATE / MULTI-TURN (⚠️ PARTIAL, 1 HIGH)
- **[H]** Schema drift `conversations.action_state` JSONB — column có trên live DB nhưng squash chain không seed → clone-DB lệch (rule#7 reproducibility, giống drift taxonomy).
- **[M]** TTL staleness — `load_state` expire theo `last_message_at` nhưng writer không update timestamp.
- **[M]** Coreference skip cho follow-up pronoun ngắn (trùng E6 — condense gate >2turn+100char).
- **[M]** condense cache serve follow-up rewrite nhầm history (Redis memo key thiếu history hash).

## 3. ACTION / SLOT-FILLING — booking (⚠️ PARTIAL, 3 HIGH) ⭐ nhánh yếu nhất
- **[H]** KHÔNG có action dispatch / completion gate — capture slot + render vào prompt NHƯNG **không bao giờ detect "đủ slot → dispatch"**. Booking không chốt được.
- **[H]** Slot-schema FORMAT MISMATCH → missing-slot list **luôn rỗng** cho format owner validated.
- **[H]** `validate_action_config` **ORPHANED** — định nghĩa nhưng không gọi đâu, không admin route persist `bots.action_config`.
- **[M]** Không per-slot type validation (mọi slot = str|None). **[M]** Slot merge không confirm/overwrite/reset policy.
- → **Nhánh action/booking gần như NON-FUNCTIONAL** (khớp memory action-slotmachine). Cần: schema-driven slot + completion gate + dispatch + wire validate + admin persist.

## 4. WEBHOOK / CALLBACK (⚠️ PARTIAL, 3 HIGH)
- **[H]** KHÔNG có delivery-attempt ledger + KHÔNG có DLQ cho callback (không bảng `webhook_deliveries`).
- **[H]** HMAC signing dùng global config, KHÔNG phải per-tenant versioned secret.
- **[H]** Ingest completion KHÔNG deliver webhook — **poll-only** (DocumentIngested chỉ vào outbox, không push tenant BE).
- **[M]** Không idempotency/delivery-id → receiver không dedupe được. **[M]** Retry backoff không jitter, envelope ngắn.
- → Cần: `webhook_deliveries` table + DLQ + per-tenant secret + idempotency-id + jittered backoff + ingest webhook push.

---

## Tổng: pipeline CORE (query) best-practice, nhánh phụ CHƯA
- Core "nhận câu → trả lời" = SOTA-grade (đã audit riêng).
- 4 nhánh phụ đều PARTIAL — đặc biệt **action/booking non-functional** + **webhook thiếu ledger/DLQ/ingest-push**.
- Ưu tiên nếu dùng: (1) action/booking nếu bot cần đặt lịch, (2) webhook nếu tenant cần async notify, (3) streaming heartbeat+error nếu dùng SSE production, (4) multi-turn schema-drift.
- Tất cả = **đề xuất, chưa code** (owner chọn nhánh nào cần).
