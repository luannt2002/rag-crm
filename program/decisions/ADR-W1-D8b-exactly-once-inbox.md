# ADR-W1-D8b — Exactly-once qua transactional inbox (process-then-mark)

> Phase 3 ADR · Wave W1 · Tier **[T1-Đúng/Đủ — data-loss]** · Date 2026-06-10
> Nguồn gap: P2-F H-EO (re-verified line-by-line) · Research: deep-research wf_38df9b83 findings 5-9 (HIGH, 3-0)
> STANCE = EVOLVE: Redis Streams + PEL/XCLAIM/NOGROUP machinery GIỮ NGUYÊN (P2-F §6.4 "đừng đụng") — chỉ đổi VỊ TRÍ + STORE của dedup-mark.

## 1. Context (SỰ THẬT)
- `redis_streams_bus.py:198-215`: dedup `SET NX` (TTL 86400s) đặt **TRƯỚC** `await handler(...)`. Handler raise → no XACK → XCLAIM redeliver → `was_new=False` → `dedup_skip` + **XACK** = message DROPPED vĩnh viễn. At-most-once.
- Consumer "DLQ" (`:348-359`) = log+XACK ở `times_delivered>5` — chết với failure-mode này vì dedup-skip XACK ngay lần 2.
- Research chốt (microservices.io + Redis docs + event-driven.io, 3-0): mark-before-handler = anti-pattern có tên; đúng = **process-then-mark, dedup INSERT trong CÙNG DB-transaction với side-effects của handler** (PK constraint làm duplicate-INSERT fail → rollback + ignore); at-least-once × idempotent-apply = effective exactly-once. Outbox publisher hiện có đã đúng at-least-once (P2-F §6.3) — không đụng.

## 2. Decision
1. **Bảng `event_inbox`** (alembic mới): `(subscriber_id VARCHAR, msg_id UUID, processed_at timestamptz DEFAULT now(), PRIMARY KEY (subscriber_id, msg_id))`. `subscriber_id` = subject+consumer-group (1 message nhiều subscriber độc lập). Retention: cron DELETE quá `DEFAULT_INBOX_RETENTION_DAYS` (constant mới, mirror dedup TTL hiện tại).
2. **Đổi thứ tự trong `_dispatch_one`**:
   - Redis `SET NX` giữ làm **fast-path hint** (skip obviously-seen, RẺ) nhưng **mất quyền XACK**: hint-hit → vẫn phải check inbox DB; chỉ inbox-row-exists mới được XACK-and-skip.
   - Handler chạy → trong CÙNG transaction của handler side-effects: `INSERT INTO event_inbox ... ON CONFLICT DO NOTHING` (handler nào không có DB-side-effect → mở tx riêng chỉ chứa INSERT — vẫn đúng vì lúc đó "apply" là no-op idempotent).
   - **XACK CHỈ SAU commit.** Crash giữa commit và XACK → redeliver → inbox-row-exists → XACK-skip (đúng exactly-once-effective).
3. **Handler contract**: handler nhận `inbox_tx` hook (session/uow) để ghi mark cùng tx — wire qua event-bus Port hiện có, signature mở rộng backward-compat (handler cũ không nhận hook → bus tự mở tx wrap mark, vẫn đúng cho handler thuần-DB-qua-uow; handler có side-effect ngoài DB phải tự idempotent — ghi rõ docstring).
4. **Consumer DLQ thật**: thay log+XACK (`:348-359`) bằng XADD sang `ragbot:{subject}:dlq` stream (persist + admin replay) rồi mới XACK. Poison message giờ replay được.

## 3. Alternatives rejected
| Alt | Lý do |
|---|---|
| Giữ Redis-only dedup, move SET NX xuống sau handler | 2 store không atomic: crash giữa handler-commit và SET NX → redeliver → double-apply. Mark phải CÙNG tx với side-effects (finding 7). |
| Swap sang Kafka EOS | Quá khổ; Redis Streams primitives đã đúng (finding 5); vi phạm EVOLVE. |
| Claim-ledger riêng (bảng claims + state machine) | Inbox PK đơn giản hơn đạt cùng guarantee; Simplicity-First. |

## 4. Implementation plan Phase 4 (failing-test-first — repro P2-F §2 làm gốc)
1. `tests/integration/test_eventbus_exactly_once.py` (fakeredis): handler raise-once-then-succeed → recover → **assert handler.call_count == 2** + XPENDING drains + side-effect applied đúng 1 lần (hiện RED: call_count=1, message dropped).
2. Test inbox idempotent: dispatch cùng msg 2 lần sau success → handler chạy 1 lần, XACK lần 2 từ inbox-hit.
3. Test crash-window: commit xong, XACK fail → redeliver → không double-apply.
4. Test DLQ: handler fail 6 lần → message nằm trong `dlq` stream (XLEN=1), XACK ở main stream, replay admin đưa lại.
5. Alembic `event_inbox` + constants. Code `redis_streams_bus.py` surgical (chỉ `_dispatch_one` + DLQ branch + recover giữ nguyên).
6. Regression: toàn bộ test bus/outbox/worker hiện có + ingest e2e (document.uploaded.v1 vẫn flow).

## 5. Gate metric
- 4 test trên GREEN; bus suite 0 regression.
- Soak nhỏ: publish 500 msg với 10% handler-fail-once → applied đúng 500, dropped 0, double-apply 0 (đếm DB).
