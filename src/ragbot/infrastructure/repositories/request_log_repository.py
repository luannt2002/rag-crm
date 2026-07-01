"""RequestLog + RequestStep repositories (v0.2.0 monitoring)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Numeric, cast, func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from ragbot.infrastructure.db.models_monitoring import (
    RequestChunkRefModel,
    RequestLogModel,
    RequestStepModel,
)
from ragbot.shared.errors import TenantIsolationViolation
from ragbot.shared.types import TenantId, WorkspaceId


class RequestLogRepository:
    """Persist + query request_logs / request_steps."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        store_plaintext: bool = False,
    ) -> None:
        """Khởi tạo repository với session factory.
        @param session_factory: async session maker của SQLAlchemy
        @param store_plaintext: when True, persist raw question/answer text on
            request_logs (verify flow). Default False keeps the Privacy-2.B
            hash-only posture; callers may always pass the plaintext and the repo
            drops it unless this is enabled.
        """
        self._sf = session_factory
        self._store_plaintext = store_plaintext

    @staticmethod
    def _ensure(record_tenant_id: TenantId | None) -> TenantId:
        """Đảm bảo record_tenant_id không None.
        @param record_tenant_id: ID tenant cần kiểm tra
        @return: record_tenant_id đã xác nhận
        """
        if record_tenant_id is None:
            raise TenantIsolationViolation("record_tenant_id required")
        return record_tenant_id

    # --- Create / update --------------------------------------------------
    async def create_request_log(
        self,
        *,
        request_id: UUID,
        record_tenant_id: TenantId,
        workspace_id: WorkspaceId,
        connect_id: str,
        question_hash: str,
        message_id: int,
        record_bot_id: UUID | None = None,
        record_conversation_id: UUID | None = None,
        trace_id: str = "",
        started_at: datetime | None = None,
        channel_type: str | None = None,
        question_text: str | None = None,
        **_kwargs: Any,
    ) -> UUID:
        """Tạo request_log row — raw question không lưu, chỉ hash.
        @param request_id: UUID request
        @param record_tenant_id: tenant ID
        @param workspace_id: slug nhánh — bot config FK chain
        @param connect_id: user/connect identifier
        @param question_hash: SHA-256 hash câu hỏi
        @param message_id: upstream message ID (BIGINT)
        @return: request_id
        """
        tid = self._ensure(record_tenant_id)
        async with self._sf() as session:
            row = RequestLogModel(
                request_id=request_id,
                record_tenant_id=tid,
                workspace_id=workspace_id,
                channel_type=channel_type,
                connect_id=connect_id,
                record_bot_id=record_bot_id,
                record_conversation_id=record_conversation_id,
                message_id=message_id,
                trace_id=trace_id,
                question_hash=question_hash,
                question_text=question_text if self._store_plaintext else None,
                started_at=started_at or datetime.now(tz=timezone.utc),
                status="running",
            )
            session.add(row)
            await session.commit()
            return request_id

    async def finalize_request_log(
        self,
        request_id: UUID,
        *,
        record_tenant_id: TenantId,
        answer_hash: str | None = None,
        answer_text: str | None = None,
        refusal_reason: str | None = None,
        record_model_id: UUID | None = None,
        model_name: str | None = None,
        agent_id: str | None = None,
        routing_reason: str | None = None,
        record_binding_id: UUID | None = None,
        binding_variant: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
        status: str = "success",
        error_code: str | None = None,
        error_message: str | None = None,
        retrieved_chunks: list[Any] | None = None,
        citations: list[Any] | None = None,
        payload_sha256: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Hoàn tất request_log — cập nhật kết quả, token, chi phí, trạng thái.
        @param request_id: UUID request cần finalize
        @param record_tenant_id: ID tenant
        """
        tid = self._ensure(record_tenant_id)
        finished = datetime.now(tz=timezone.utc)
        async with self._sf() as session:
            row = await session.get(RequestLogModel, request_id)
            if row is None or row.record_tenant_id != tid:
                raise TenantIsolationViolation(
                    f"request_log {request_id} not found in tenant {tid}",
                )
            duration_ms = int((finished - row.started_at).total_seconds() * 1000)
            row.answer_hash = answer_hash
            if self._store_plaintext:
                row.answer_text = answer_text
            row.refusal_reason = refusal_reason
            row.record_model_id = record_model_id
            row.model_name = model_name
            row.agent_id = agent_id
            row.routing_reason = routing_reason
            row.record_binding_id = record_binding_id
            row.binding_variant = binding_variant
            row.finished_at = finished
            row.duration_ms = duration_ms
            row.prompt_tokens = prompt_tokens
            row.completion_tokens = completion_tokens
            row.total_tokens = prompt_tokens + completion_tokens
            row.cost_usd = Decimal(str(cost_usd))
            row.status = status
            row.error_code = error_code
            row.error_message = error_message
            # ``retrieved_chunks`` JSONB column dropped in alembic 0109 (G15) -
            # write refs into ``request_chunk_refs`` child table. Caller still
            # passes the dict-list shape it always did; we extract chunk_id +
            # rank + score here and skip rows whose chunk_id is missing /
            # not-a-uuid (defensive: legacy callers in the worker tree pass
            # only ``chunk_index`` for forensic preview).
            session.add_all(self._build_chunk_refs(request_id, retrieved_chunks))
            row.citations = citations or []
            row.payload_sha256 = payload_sha256
            if metadata is not None:
                row.metadata_json = {**(row.metadata_json or {}), **metadata}

            # Durable monitoring mirror (alembic 0217): append-only, NO FK, so a
            # bot delete / per-bot clear never wipes it. One cheap INSERT in the
            # same txn — start/finish/duration + tokens + cost survive resets so
            # day-by-day monitoring + billing can always be reconstructed.
            await session.execute(text("""
                INSERT INTO monitoring_log (
                    request_id, record_tenant_id, record_bot_id, bot_id,
                    workspace_id, channel_type, started_at, finished_at,
                    duration_ms, prompt_tokens, completion_tokens, total_tokens,
                    cost_usd, model_name, status
                ) VALUES (
                    :request_id, :tid, :rbid,
                    (SELECT bot_id FROM bots WHERE id = :rbid LIMIT 1),
                    :ws, :ch, :started, :finished, :dur,
                    :pt, :ct, :tt, :cost, :model, :status
                )
            """), {
                "request_id": request_id, "tid": tid, "rbid": row.record_bot_id,
                "ws": row.workspace_id, "ch": row.channel_type,
                "started": row.started_at, "finished": finished, "dur": duration_ms,
                "pt": prompt_tokens, "ct": completion_tokens,
                "tt": prompt_tokens + completion_tokens,
                "cost": Decimal(str(cost_usd)), "model": model_name, "status": status,
            })
            await session.commit()

    @staticmethod
    def _build_chunk_refs(
        request_id: UUID,
        retrieved_chunks: list[Any] | None,
    ) -> list[RequestChunkRefModel]:
        """Map caller-supplied chunk-dicts to ``RequestChunkRefModel`` rows.

        Skips entries that lack a parseable ``chunk_id`` (or ``id`` legacy
        synonym): the new relational table is FK-constrained to
        ``document_chunks.id`` so a NULL / non-UUID would fail the INSERT.
        Pre-G15 callers wrote inline JSONB with only ``chunk_index`` for
        UI previews -- those rows are silently dropped here (they were
        never analysable anyway).
        """
        if not retrieved_chunks:
            return []
        out: list[RequestChunkRefModel] = []
        for idx, raw in enumerate(retrieved_chunks):
            if not isinstance(raw, dict):
                continue
            cid_raw = raw.get("chunk_id") or raw.get("id")
            if not cid_raw:
                continue
            try:
                cid = UUID(str(cid_raw))
            except (ValueError, TypeError):
                continue
            try:
                rank_val = int(raw.get("rank", idx))
            except (TypeError, ValueError):
                rank_val = idx
            score_raw = raw.get("score")
            score_val: Decimal | None = None
            if score_raw is not None and score_raw != "":
                try:
                    score_val = Decimal(str(score_raw))
                except (ValueError, TypeError, InvalidOperation):
                    score_val = None
            out.append(RequestChunkRefModel(
                id=uuid4(),
                record_request_id=request_id,
                record_chunk_id=cid,
                rank=rank_val,
                score=score_val,
            ))
        return out

    async def scrub_pii_for_conversation(
        self,
        record_conversation_id: UUID,
        *,
        record_tenant_id: TenantId,
    ) -> int:
        """GDPR right-to-erasure: was a JSONB-column scrub before alembic 0109.

        Pre-G15 the ``request_logs.retrieved_chunks`` JSONB column held PII
        chunk previews (up to ``DEFAULT_LOG_PREVIEW_CHARS`` each); GDPR
        erase nullified that column for the conversation.

        Post-G15 the column is dropped and the only surviving artifact is
        the relational ``request_chunk_refs`` table which carries ONLY
        (request_id, chunk_id, rank, score) -- no PII. We keep the
        method's tenant-isolation contract (admin_gdpr emits a forensic
        audit row using the returned count) by returning the COUNT of
        request_log rows owning this conversation -- callers still see
        a non-zero number, and no PII has ever been lost.

        @return: number of request_log rows in the conversation.
        """
        tid = self._ensure(record_tenant_id)
        async with self._sf() as session:
            count_stmt = (
                select(func.count())
                .select_from(RequestLogModel)
                .where(
                    RequestLogModel.record_conversation_id == record_conversation_id,
                    RequestLogModel.record_tenant_id == tid,
                )
            )
            return int((await session.execute(count_stmt)).scalar_one() or 0)

    async def add_step(
        self,
        *,
        request_id: UUID,
        record_tenant_id: TenantId,
        step_name: str,
        step_order: int,
        duration_ms: int,
        model_used: str | None = None,
        record_binding_id: UUID | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        status: str = "success",
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> UUID:
        """Thêm một bước xử lý (step) vào request_log.
        @param request_id: UUID request cha
        @param step_name: tên bước (retrieve, generate, ...)
        @return: UUID của step vừa tạo
        """
        tid = self._ensure(record_tenant_id)
        async with self._sf() as session:
            # request_steps inherits the parent request_log's slug —
            # single source of truth via the FK chain. One extra round
            # trip is acceptable; the alternative (threading slug from
            # caller) leaks the denormalisation rule out of the repo.
            parent_ws = await session.scalar(
                select(RequestLogModel.workspace_id).where(
                    RequestLogModel.request_id == request_id,
                ),
            )
            if parent_ws is None:
                raise TenantIsolationViolation(
                    f"request_log {request_id} not found — cannot add step",
                )
            step = RequestStepModel(
                id=uuid4(),
                record_request_id=request_id,
                record_tenant_id=tid,
                workspace_id=parent_ws,
                step_name=step_name,
                step_order=step_order,
                model_used=model_used,
                record_binding_id=record_binding_id,
                duration_ms=duration_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=Decimal(str(cost_usd)),
                status=status,
                error=error,
                metadata_json=metadata or {},
            )
            session.add(step)
            await session.commit()
            return step.id

    async def add_steps_batch(
        self,
        *,
        request_id: UUID,
        record_tenant_id: TenantId,
        steps: Sequence[dict[str, Any]],
    ) -> int:
        """Phase-B B4 — persist many ``request_steps`` rows in one round-trip.

        Replaces the per-step ``add_step()`` write loop (1 INSERT + 1
        commit per pipeline stage, ~27 stages per chat turn) with a
        single batched flush. Parent ``workspace_id`` is read ONCE for
        the whole batch so the FK denormalisation rule still lives in
        the repo (callers do not thread the slug).

        ``steps`` is an ordered sequence of dicts. Each dict mirrors
        the kwargs accepted by ``add_step`` (``step_name``,
        ``step_order``, ``duration_ms`` REQUIRED; ``model_used``,
        ``record_binding_id``, ``input_tokens``, ``output_tokens``,
        ``cost_usd``, ``status``, ``error``, ``metadata`` OPTIONAL).

        @return: count of rows actually persisted (== ``len(steps)`` on success).
        """
        if not steps:
            return 0
        tid = self._ensure(record_tenant_id)
        async with self._sf() as session:
            parent_ws = await session.scalar(
                select(RequestLogModel.workspace_id).where(
                    RequestLogModel.request_id == request_id,
                ),
            )
            if parent_ws is None:
                raise TenantIsolationViolation(
                    f"request_log {request_id} not found — cannot add steps batch",
                )
            rows: list[RequestStepModel] = []
            for raw in steps:
                rows.append(RequestStepModel(
                    id=uuid4(),
                    record_request_id=request_id,
                    record_tenant_id=tid,
                    workspace_id=parent_ws,
                    step_name=raw["step_name"],
                    step_order=int(raw["step_order"]),
                    model_used=raw.get("model_used"),
                    record_binding_id=raw.get("record_binding_id"),
                    duration_ms=int(raw.get("duration_ms", 0)),
                    input_tokens=int(raw.get("input_tokens", 0)),
                    output_tokens=int(raw.get("output_tokens", 0)),
                    cost_usd=Decimal(str(raw.get("cost_usd", 0.0))),
                    status=raw.get("status", "success"),
                    error=raw.get("error"),
                    metadata_json=raw.get("metadata") or {},
                ))
            session.add_all(rows)
            await session.commit()
            return len(rows)

    async def attach_feedback_by_message(
        self,
        *,
        record_tenant_id: TenantId,
        message_id: int,
        score: int,
        is_correct: bool | None = None,
        comment: str | None = None,
    ) -> int:
        """Attach feedback for the newest request_log matching (tenant, msg).

        Returns the count of updated rows (0 if none found).
        """
        tid = self._ensure(record_tenant_id)
        async with self._sf() as session:
            values: dict[str, Any] = {
                "feedback_score": score,
                "is_correct": is_correct,
                "quality_evaluated_at": datetime.now(tz=timezone.utc),
                "quality_evaluator": "human",
            }
            if comment is not None:
                values["feedback_comment"] = comment
            res = await session.execute(
                update(RequestLogModel)
                .where(
                    RequestLogModel.record_tenant_id == tid,
                    RequestLogModel.message_id == message_id,
                )
                .values(**values),
            )
            await session.commit()
            return int(res.rowcount or 0)

    async def attach_feedback(
        self,
        request_id: UUID,
        *,
        record_tenant_id: TenantId,
        score: int,
        is_correct: bool | None = None,
        comment: str | None = None,
    ) -> None:
        """Attach user feedback to a request_log row.

        Migration 0010: `feedback` table dropped — score / is_correct /
        comment now live on `request_logs` directly.
        """
        tid = self._ensure(record_tenant_id)
        async with self._sf() as session:
            values: dict[str, Any] = {
                "feedback_score": score,
                "is_correct": is_correct,
                "quality_evaluated_at": datetime.now(tz=timezone.utc),
                "quality_evaluator": "human",
            }
            if comment is not None:
                values["feedback_comment"] = comment
            await session.execute(
                update(RequestLogModel)
                .where(
                    RequestLogModel.request_id == request_id,
                    RequestLogModel.record_tenant_id == tid,
                )
                .values(**values),
            )
            await session.commit()

    # --- Query / metrics --------------------------------------------------
    async def get_overview(
        self,
        *,
        record_tenant_id: TenantId,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        record_bot_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Lấy tổng quan thống kê request (tổng, thành công, lỗi, chi phí, ...).
        @param record_tenant_id: ID tenant
        @param date_from: thời gian bắt đầu (tuỳ chọn)
        @param date_to: thời gian kết thúc (tuỳ chọn)
        @return: dict chứa các chỉ số overview
        """
        tid = self._ensure(record_tenant_id)
        async with self._sf() as session:
            base = select(RequestLogModel).where(RequestLogModel.record_tenant_id == tid)
            if date_from:
                base = base.where(RequestLogModel.started_at >= date_from)
            if date_to:
                base = base.where(RequestLogModel.started_at <= date_to)
            if record_bot_id:
                base = base.where(RequestLogModel.record_bot_id == record_bot_id)

            sq = base.subquery()

            q = select(
                func.count().label("total"),
                func.count().filter(sq.c.status == "success").label("success"),
                func.count().filter(sq.c.status == "failed").label("failed"),
                func.count().filter(sq.c.status == "timeout").label("timeout"),
                func.avg(sq.c.duration_ms).label("avg_duration"),
                func.avg(sq.c.total_tokens).label("avg_tokens"),
                func.sum(sq.c.cost_usd).label("total_cost"),
                func.count().filter(sq.c.is_correct.is_(True)).label("correct"),
                func.count().filter(sq.c.is_correct.isnot(None)).label("evaluated"),
            ).select_from(sq)

            row = (await session.execute(q)).one()

            total = row.total or 0
            failed = row.failed or 0
            timeout = row.timeout or 0
            evaluated = row.evaluated or 0
            correct = row.correct or 0
            accuracy = float(correct) / float(evaluated) if evaluated > 0 else None

            return {
                "total_requests": int(total),
                "success": int(row.success or 0),
                "failed": int(failed),
                "timeout": int(timeout),
                "error_rate": (failed + timeout) / total if total else 0.0,
                "avg_duration_ms": float(row.avg_duration or 0),
                "avg_tokens": float(row.avg_tokens or 0),
                "total_cost_usd": float(row.total_cost or 0),
                "accuracy": accuracy,
                "evaluated": int(evaluated),
            }

    async def get_metrics_by_model(
        self,
        *,
        record_tenant_id: TenantId,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Lấy thống kê hiệu suất nhóm theo model (token, chi phí, độ chính xác).
        @param record_tenant_id: ID tenant
        @return: danh sách dict thống kê theo từng model
        """
        tid = self._ensure(record_tenant_id)
        async with self._sf() as session:
            stmt = (
                select(
                    RequestLogModel.record_model_id,
                    RequestLogModel.model_name,
                    func.count().label("requests"),
                    func.avg(RequestLogModel.duration_ms).label("avg_duration_ms"),
                    func.sum(RequestLogModel.total_tokens).label("total_tokens"),
                    func.sum(RequestLogModel.cost_usd).label("total_cost_usd"),
                    func.sum(
                        cast(RequestLogModel.is_correct, Numeric),
                    ).label("correct"),
                    func.count(RequestLogModel.is_correct).label("evaluated"),
                )
                .where(RequestLogModel.record_tenant_id == tid)
                .group_by(RequestLogModel.record_model_id, RequestLogModel.model_name)
            )
            if date_from:
                stmt = stmt.where(RequestLogModel.started_at >= date_from)
            if date_to:
                stmt = stmt.where(RequestLogModel.started_at <= date_to)

            rows = (await session.execute(stmt)).all()
            out: list[dict[str, Any]] = []
            for r in rows:
                evaluated = int(r.evaluated or 0)
                correct = int(r.correct or 0)
                out.append(
                    {
                        "model_id": str(r.record_model_id) if r.record_model_id else None,
                        "model_name": r.model_name,
                        "requests": int(r.requests),
                        "avg_duration_ms": float(r.avg_duration_ms or 0),
                        "total_tokens": int(r.total_tokens or 0),
                        "total_cost_usd": float(r.total_cost_usd or 0),
                        "accuracy": correct / evaluated if evaluated > 0 else None,
                    },
                )
            return out

    async def get_top_questions(
        self,
        *,
        record_tenant_id: TenantId,
        limit: int = 20,
        only_failed: bool = False,
    ) -> Sequence[dict[str, Any]]:
        """Lấy top câu hỏi được hỏi nhiều nhất (theo question_hash).
        @param tenant_id: ID tenant
        @param limit: số lượng tối đa
        @param only_failed: chỉ lấy câu hỏi có đánh giá sai
        @return: danh sách dict {question_hash, count}
        """
        tid = self._ensure(record_tenant_id)
        async with self._sf() as session:
            # Privacy 2.B: GROUP BY question_hash (opaque 64-hex). Clients that
            # need the raw question must JOIN `messages` per-row with admin scope.
            stmt = (
                select(
                    RequestLogModel.question_hash,
                    func.count().label("count"),
                )
                .where(RequestLogModel.record_tenant_id == tid)
                .group_by(RequestLogModel.question_hash)
                .order_by(func.count().desc())
                .limit(limit)
            )
            if only_failed:
                stmt = stmt.where(RequestLogModel.is_correct.is_(False))
            rows = (await session.execute(stmt)).all()
            return [
                {"question_hash": r.question_hash, "count": int(r.count)}
                for r in rows
            ]

    async def get_step_breakdown(
        self,
        *,
        record_tenant_id: TenantId,
        date_from: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Phân tích thời gian xử lý theo từng bước (avg, p95).
        @param tenant_id: ID tenant
        @return: danh sách dict thống kê theo step_name
        """
        tid = self._ensure(record_tenant_id)
        async with self._sf() as session:
            stmt = (
                select(
                    RequestStepModel.step_name,
                    func.count().label("samples"),
                    func.avg(RequestStepModel.duration_ms).label("avg_duration_ms"),
                    func.percentile_cont(0.95).within_group(
                        RequestStepModel.duration_ms.asc(),
                    ).label("p95"),
                )
                .where(RequestStepModel.record_tenant_id == tid)
                .group_by(RequestStepModel.step_name)
                .order_by(func.avg(RequestStepModel.duration_ms).desc())
            )
            if date_from:
                stmt = stmt.where(RequestStepModel.started_at >= date_from)
            rows = (await session.execute(stmt)).all()
            return [
                {
                    "step_name": r.step_name,
                    "samples": int(r.samples),
                    "avg_duration_ms": float(r.avg_duration_ms or 0),
                    "p95_duration_ms": float(r.p95 or 0),
                }
                for r in rows
            ]


__all__ = ["RequestLogRepository"]
