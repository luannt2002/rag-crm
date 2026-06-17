"""SpeculativeRouter — Wave K1 Phase 2 of the Speculative Streaming roadmap.

Race a cheap draft LLM against the main LLM. Whichever returns its first
chunk first streams to the client; the loser is cancelled. Paper claim
(``Speculative Decoding``, Leviathan et al. 2023; ``Online Speculative
Decoding``, Liu et al. 2024) — TTFB p50 1.5s → 350ms on draft-friendly
prompts.

Hexagonal placement: this is a Strategy implementation of ``LLMPort`` that
wraps two other LLMPort implementations injected at construction time
(the Strategy + DI pattern enforced by ``CLAUDE.md``). Adding a new race
policy is a new file under ``infrastructure/llm/`` — orchestration code
never branches on ``if provider == ...``.

CRITICAL — HALLU=0 sacred:
    Phase 2 does NOT verify draft tokens against the main model. If the
    draft wins and fabricates a citation/number, the user sees the
    fabrication. Default OFF (``DEFAULT_SPECULATIVE_STREAMING_ENABLED =
    False``) is mandatory until Phase 3's HALLU verifier ships. Per-bot
    opt-in only after the bot owner has accepted the fabrication risk.

Cancel semantics:
    ``asyncio.wait(FIRST_COMPLETED)`` returns as soon as one task's
    ``complete_runtime_stream`` coroutine resolves to an async iterator
    (i.e. the upstream HTTP connection is open and the first chunk is
    in-flight, NOT once the first token has been yielded). The loser
    task is ``cancel()``-ed; its underlying httpx connection is closed
    by litellm's stream context manager on ``CancelledError``. We then
    drain ``CancelledError`` from the loser with a short timeout so the
    cancel can't leak across the boundary.

Cost note:
    Both LLMs are billed for whatever tokens they generated before
    cancel. The structlog ``speculative_loser_cost_usd`` event lets
    operators measure the cost premium and decide whether the latency
    win pays for itself per-bot.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import structlog

from ragbot.application.ports.llm_port import LLMMessage, LLMPort, LLMResponse
from ragbot.shared.constants import DEFAULT_DRAFT_MODEL_TIMEOUT_S

if TYPE_CHECKING:
    from ragbot.application.dto.ai_specs import EmbeddingSpec
    from ragbot.application.services.hallu_verifier import HALLUVerifier
    from ragbot.shared.types import TenantId

logger = structlog.get_logger(__name__)

# Wire sentinel surfaced by the streaming entrypoint when the verifier
# rejects the draft and we must roll back to the main stream. SSE helpers
# downstream translate this into a typed ``redo`` event (data + retry
# hint) so the client throws away the buffered draft and waits for the
# main tokens that follow.
SPECULATIVE_REDO_SENTINEL: str = "__SPECULATIVE_REDO__"


async def _aclose_silently(gen: AsyncIterator[str]) -> None:
    """Close an async generator without raising.

    Used to release upstream HTTP connections on the loser generator after
    the race completes.  Errors during close are swallowed and debug-logged
    so they cannot interrupt the winner stream.
    """
    try:
        await gen.aclose()  # type: ignore[union-attr]
    except Exception as exc:  # noqa: BLE001 — generator cleanup, best-effort
        logger.debug("speculative_gen_close_error", error=type(exc).__name__)


class SpeculativeRouter(LLMPort):
    """Race two LLMPort implementations; winner streams, loser cancels.

    Construction:
        ``SpeculativeRouter(main_llm=..., draft_llm=...)`` — both args
        are full ``LLMPort`` instances. The router holds no provider /
        model state of its own — that lives on the wrapped instances
        and the per-call ``cfg`` argument.

    Routing:
        Only ``complete_runtime_stream`` honors the race. The non-
        streaming entrypoints (``complete``, ``complete_runtime``,
        ``stream``, ``health_check``, ``refresh_routing``, ``close``)
        delegate straight to ``main_llm`` so background / structured
        callers continue to hit the main model deterministically.
    """

    def __init__(
        self,
        *,
        main_llm: LLMPort,
        draft_llm: LLMPort,
        draft_timeout_s: float = DEFAULT_DRAFT_MODEL_TIMEOUT_S,
        hallu_verifier: HALLUVerifier | None = None,
    ) -> None:
        self._main_llm = main_llm
        self._draft_llm = draft_llm
        self._draft_timeout_s = float(draft_timeout_s)
        # Phase 3 (Wave K2) verifier. When ``None`` the router behaves as
        # Phase 2 (race + cancel loser). When set, the streaming entry
        # ALSO honors a per-call ``verify_enabled`` kwarg to buffer +
        # verify draft tokens against the main first chunk before
        # streaming the draft to the client (HALLU=0 sacred path).
        self._hallu_verifier = hallu_verifier

    # ---- LLMPort surface — non-streaming delegates straight to main ----

    async def health_check(self) -> bool:
        return await self._main_llm.health_check()

    async def complete(self, *args: Any, **kwargs: Any) -> Any:
        # ``draft_model`` kwarg is reserved for future stateless racing
        # at the non-streaming layer; Phase 2 honors it only inside the
        # streaming entrypoint. Drop it silently here so wrappers stay
        # compatible with the extended Protocol.
        kwargs.pop("draft_model", None)
        return await self._main_llm.complete(*args, **kwargs)

    async def stream(
        self,
        messages: list[LLMMessage],
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        return await self._main_llm.stream(messages, **kwargs)

    async def refresh_routing(self) -> None:
        # Both wrappers may share the same repo; refreshing each is
        # idempotent and cheap (Redis-cached model list).
        await asyncio.gather(
            self._main_llm.refresh_routing(),
            self._draft_llm.refresh_routing(),
            return_exceptions=True,
        )

    async def close(self) -> None:
        await asyncio.gather(
            self._main_llm.close(),
            self._draft_llm.close(),
            return_exceptions=True,
        )

    # ---- Speculative streaming entrypoint ------------------------------

    @staticmethod
    async def _race_first_token(
        gen: AsyncIterator[str],
    ) -> tuple[str | None, AsyncIterator[str]]:
        """Wrap an async generator: pull its first token and return it.

        Returns ``(first_token, gen)`` when the generator yields at least
        one token, or ``(None, gen)`` when the generator is immediately
        exhausted.  Raises if the generator raises before its first yield.

        This helper is scheduled via ``asyncio.create_task`` so two async
        generators can race concurrently.  ``asyncio.create_task`` requires
        a *coroutine object* (not an async generator object); this wrapper
        converts the async generator into a coroutine that blocks only until
        the first token arrives.
        """
        try:
            first = await gen.__anext__()
        except StopAsyncIteration:
            return None, gen
        return first, gen

    async def complete_runtime_stream(
        self,
        cfg: Any,
        messages: list[dict],
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Race draft vs main; yield tokens from the first to produce one.

        Two async generators are started and raced via ``asyncio.wait``
        (``return_when=FIRST_COMPLETED``).  Because ``asyncio.create_task``
        requires a *coroutine*, each generator is wrapped in
        ``_race_first_token`` — a coroutine that resolves when the generator
        yields its first token.  The winner's first token is replayed first,
        then the remaining tokens are streamed; the loser is cancelled.

        The winner's tokens are forwarded verbatim. If the winner raises
        mid-stream we re-raise; we do NOT silently fall back to the loser
        (which has already been cancelled by then). Callers wanting
        graceful fallback should layer that above ``LLMPort``.

        Phase 3 verifier gate (kwargs):
            ``verify_enabled`` (bool, default False) — when True and
                ``hallu_verifier`` was wired at construction, draft-wins
                turns buffer the first ``buffer_tokens`` deltas, await
                the main task's first chunk, run the verifier, and
                either flush + continue the draft (safe) or drop the
                draft and stream main (emitting ``SPECULATIVE_REDO_SENTINEL``
                first so the SSE wire can emit a ``redo`` event).
            ``verify_embed_spec`` (EmbeddingSpec | None) — passed
                through to ``verify_draft_vs_main`` so gate 3 (topic
                divergence) fires. ``None`` skips gate 3.
            ``verify_record_tenant_id`` (TenantId | None) — tenant scope
                for the embed call when gate 3 is on.

            All three kwargs are popped before delegating to the wrapped
            LLMs so the underlying router never sees them.
        """
        verify_enabled = bool(kwargs.pop("verify_enabled", False))
        verify_embed_spec: EmbeddingSpec | None = kwargs.pop(
            "verify_embed_spec", None,
        )
        verify_record_tenant_id: TenantId | None = kwargs.pop(
            "verify_record_tenant_id", None,
        )
        main_cfg = cfg
        draft_cfg = self._build_draft_cfg(cfg, kwargs.pop("draft_model", None))

        t0 = time.monotonic()

        # Start both async generators, then race them to their first token.
        # ``complete_runtime_stream`` on wrapped LLMs returns an async
        # generator object (not a coroutine), so we wrap each in
        # ``_race_first_token`` which IS a coroutine and is therefore
        # compatible with ``asyncio.create_task``.
        draft_gen = self._draft_llm.complete_runtime_stream(draft_cfg, messages, **kwargs)
        main_gen = self._main_llm.complete_runtime_stream(main_cfg, messages, **kwargs)

        draft_task = asyncio.create_task(self._race_first_token(draft_gen))
        main_task = asyncio.create_task(self._race_first_token(main_gen))

        try:
            done, pending = await asyncio.wait(
                {draft_task, main_task},
                return_when=asyncio.FIRST_COMPLETED,
                timeout=self._draft_timeout_s,
            )
        except asyncio.CancelledError:
            draft_task.cancel()
            main_task.cancel()
            await self._drain_cancelled(draft_task, main_task)
            raise

        if not done:
            # Timeout: neither side opened its stream within the budget.
            # Cancel both and surface the timeout to the caller — same
            # contract as a single-model stall.
            draft_task.cancel()
            main_task.cancel()
            await self._drain_cancelled(draft_task, main_task)
            raise TimeoutError(
                f"speculative router: neither draft nor main returned "
                f"a stream within {self._draft_timeout_s}s",
            )

        # ``done`` is a set; pick whichever task finished first. We may
        # have BOTH in ``done`` if they raced to completion in the same
        # scheduler tick — prefer the draft (lower cost) in that case.
        if draft_task in done:
            winner_task = draft_task
            loser_task = main_task
            loser_gen = main_gen
            source = "draft"
        else:
            winner_task = main_task
            loser_task = draft_task
            loser_gen = draft_gen
            source = "main"

        winner_first_token_ms = int((time.monotonic() - t0) * 1000)

        # Determine if the winner raised; if so, cancel the loser, then re-raise.
        winner_exc = winner_task.exception()
        if winner_exc is not None:
            loser_task.cancel()
            await self._drain_cancelled(loser_task)
            # Also close the loser generator to release upstream resources.
            await _aclose_silently(loser_gen)
            logger.warning(
                "speculative_winner_failed",
                source=source,
                error=type(winner_exc).__name__,
            )
            raise winner_exc

        winner_first_tok, winner_gen = winner_task.result()

        # Phase 3 verifier gate — only fires when (a) draft won the race
        # (so there is something to verify), (b) verifier was wired at
        # construction, AND (c) the caller opted in for this turn. Other
        # branches keep the Phase 2 "cancel loser, stream winner" path.
        verify_active = (
            source == "draft"
            and self._hallu_verifier is not None
            and verify_enabled
        )
        if verify_active:
            # Pass the first token already pulled by _race_first_token so the
            # verifier can include it in its buffer without re-iterating.
            async for tok in self._stream_draft_with_verify(
                draft_first_tok=winner_first_tok,
                draft_gen=winner_gen,
                main_task=loser_task,
                main_gen=loser_gen,
                spec=verify_embed_spec,
                record_tenant_id=verify_record_tenant_id,
                winner_first_token_ms=winner_first_token_ms,
                t0=t0,
            ):
                yield tok
            return

        # Cancel loser BEFORE we start streaming so the cancel races with
        # whatever provider work it was about to do.
        loser_cancel_t0 = time.monotonic()
        loser_task.cancel()
        loser_cancel_ms = int((loser_cancel_t0 - t0) * 1000)

        logger.info(
            "speculative_winner",
            source=source,
            winner_first_token_ms=winner_first_token_ms,
            loser_cancelled_at_ms=loser_cancel_ms,
        )

        # Drain the loser in the background — we don't want to block
        # streaming on it, but we also can't leave the task pending or
        # the underlying httpx connection may stay open. Track cost via
        # a background coroutine.
        asyncio.create_task(self._drain_loser_and_emit_cost(loser_task, source))
        # Close the loser generator so its upstream HTTP connection is
        # released (the drain task covers the task, not the generator).
        asyncio.create_task(_aclose_silently(loser_gen))

        # Replay the first token pulled by ``_race_first_token``, then
        # stream the remainder of the winner generator.
        if winner_first_tok is not None:
            yield winner_first_tok
        async for token in winner_gen:
            yield token

    # ---- Phase 3 verifier streaming ------------------------------------

    async def _stream_draft_with_verify(
        self,
        *,
        draft_first_tok: str | None,
        draft_gen: AsyncIterator[str],
        main_task: asyncio.Task,
        main_gen: AsyncIterator[str],
        spec: EmbeddingSpec | None,
        record_tenant_id: TenantId | None,
        winner_first_token_ms: int,
        t0: float,
    ) -> AsyncIterator[str]:
        """Stream the draft only after verifier accepts vs main first chunk.

        Buffer ``hallu_verifier.buffer_tokens`` worth of draft deltas
        first; in parallel we wait for the main task to finish its
        ``_race_first_token`` race and yield its first chunk. We then
        compare draft vs main; on ``safe`` we cancel main, flush buffered
        deltas, and continue streaming from the draft. On ``unsafe`` we
        drop the draft, emit ``SPECULATIVE_REDO_SENTINEL`` so the SSE wire
        can fire a ``redo`` event, and stream main verbatim.

        ``draft_first_tok`` is the token already pulled by
        ``_race_first_token`` so this method can include it in the buffer
        without re-iterating the generator.

        Honest gate semantics:
            - HALLU=0 sacred: any verifier rejection switches to main.
            - We NEVER silently accept a draft we couldn't verify; an
              unhandled exception during verify falls back to ``unsafe``.
        """
        verifier = self._hallu_verifier
        # mypy / runtime guard; verifier is non-None by the caller.
        assert verifier is not None

        buffer_size = int(verifier.buffer_tokens) or 1

        # Step 1 — accumulate up to ``buffer_size`` draft deltas (or
        # stream end, whichever comes first) before requesting verify.
        # Seed the buffer with the first token already pulled by the race.
        draft_buffer: list[str] = []
        draft_exhausted = False
        if draft_first_tok is not None:
            draft_buffer.append(draft_first_tok)
        try:
            for _ in range(buffer_size - len(draft_buffer)):
                try:
                    tok = await draft_gen.__anext__()
                except StopAsyncIteration:
                    draft_exhausted = True
                    break
                draft_buffer.append(tok)
        except asyncio.CancelledError:
            main_task.cancel()
            await _aclose_silently(main_gen)
            raise

        # Step 2 — wait for the main ``_race_first_token`` task to
        # complete so we can pull its first chunk for the cross-check.
        # A short timeout treats non-responsive main as ``unsafe``.
        main_first_tok: str | None = None
        main_iter: AsyncIterator[str] | None = None
        try:
            await asyncio.wait_for(
                asyncio.shield(main_task),
                timeout=self._draft_timeout_s,
            )
            main_first_tok, main_iter = main_task.result()
        except (asyncio.TimeoutError, asyncio.CancelledError):
            main_task.cancel()
            await self._drain_cancelled(main_task)
            await _aclose_silently(main_gen)
            logger.warning(
                "speculative_verify_main_open_timeout",
                draft_buffer_tokens=len(draft_buffer),
            )
            await _aclose_silently(draft_gen)
            yield SPECULATIVE_REDO_SENTINEL
            return
        except BaseException as exc:  # noqa: BLE001 — main side failed; fall back to safe path unavailable
            logger.warning(
                "speculative_verify_main_open_failed",
                error=type(exc).__name__,
            )
            await _aclose_silently(draft_gen)
            raise

        # Use main_gen as iterator fallback when main_iter is None (empty stream).
        main_iter = main_iter if main_iter is not None else main_gen

        # Seed main_first_chunk with the token already pulled by main race.
        main_first_chunk = main_first_tok or ""
        if not main_first_chunk:
            # Pull one more chunk from main if its first token was empty.
            try:
                async for chunk in main_iter:
                    if chunk:
                        main_first_chunk = chunk
                        break
            except asyncio.CancelledError:
                await _aclose_silently(draft_gen)
                raise

        # Step 3 — run the verifier. Failure → unsafe (HALLU sacred).
        try:
            verdict = await verifier.verify_draft_vs_main(
                draft_buffer,
                main_first_chunk,
                spec=spec,
                record_tenant_id=record_tenant_id,
            )
            safe = bool(verdict.safe)
            reason = verdict.reason
            overlap = float(verdict.overlap_pct)
        except Exception as exc:  # noqa: BLE001 — verifier failure must NOT silent-accept the draft (HALLU sacred); log + force redo
            logger.warning(
                "speculative_verify_exception_forcing_redo",
                error=type(exc).__name__,
            )
            safe = False
            reason = "verifier_exception"
            overlap = 0.0

        verify_ms = int((time.monotonic() - t0) * 1000) - winner_first_token_ms
        logger.info(
            "speculative_verify",
            safe=safe,
            reason=reason,
            overlap_pct=overlap,
            draft_buffer_tokens=len(draft_buffer),
            verify_latency_ms=verify_ms,
            winner_first_token_ms=winner_first_token_ms,
        )

        if safe:
            # Cancel main now that we trust the draft, then flush the
            # buffered draft tokens + continue streaming from the draft
            # iterator until exhaustion.
            main_task.cancel()
            asyncio.create_task(self._drain_loser_and_emit_cost(main_task, "draft"))
            asyncio.create_task(_aclose_silently(main_gen))
            for tok in draft_buffer:
                yield tok
            if draft_exhausted:
                return
            async for tok in draft_gen:
                yield tok
            return

        # UNSAFE path — HALLU sacred. Drop the draft entirely and stream
        # the main model from this point on. Surface the redo sentinel
        # FIRST so the SSE wire can emit a typed event to the client
        # (drop buffered output, switch to main).
        asyncio.create_task(_aclose_silently(draft_gen))
        yield SPECULATIVE_REDO_SENTINEL
        # Replay the main first chunk we already pulled, then stream the
        # rest of main verbatim.
        if main_first_chunk:
            yield main_first_chunk
        try:
            async for tok in main_iter:
                yield tok
        except asyncio.CancelledError:
            raise

    # ---- Helpers -------------------------------------------------------

    @staticmethod
    def _build_draft_cfg(cfg: Any, draft_model: str | None) -> Any:
        """Project ``cfg`` onto the draft model wire name.

        Empty / None ``draft_model`` falls back to the main wire name —
        which makes the race a tail-latency hedge between two identical
        calls (still useful when the provider's tail is heavy). Operators
        opt out of this hedge by leaving ``speculative_streaming_enabled
        = False``.
        """
        if not draft_model:
            return cfg
        # Use ``dataclasses.replace`` when the runtime cfg is a dataclass;
        # otherwise fall back to shallow attribute clone via SimpleNamespace
        # so tests can pass plain stub objects without dragging the full
        # ModelRuntimeConfig DTO into the unit boundary.
        from dataclasses import is_dataclass, replace

        if is_dataclass(cfg):
            try:
                return replace(cfg, litellm_name=draft_model)
            except TypeError:
                pass
        # Best-effort: mutate a shallow copy. We avoid mutating the input
        # to preserve caller invariants.
        from copy import copy

        new = copy(cfg)
        try:
            new.litellm_name = draft_model
        except AttributeError:
            return cfg
        return new

    @staticmethod
    async def _drain_cancelled(*tasks: asyncio.Task) -> None:
        """Await cancelled tasks, swallowing ``CancelledError``."""
        for t in tasks:
            if t.done():
                # Surface non-cancel exceptions in debug log; the caller
                # already handled the primary outcome.
                try:
                    t.result()
                except (asyncio.CancelledError, Exception) as exc:  # noqa: BLE001
                    logger.debug(
                        "speculative_drain_cancelled_done",
                        error=type(exc).__name__,
                    )
                continue
            try:
                await asyncio.wait_for(t, timeout=DEFAULT_DRAFT_MODEL_TIMEOUT_S)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception as exc:  # noqa: BLE001 — cleanup path must swallow any provider tail to avoid leaking past cancellation
                logger.debug(
                    "speculative_drain_cancelled_exception",
                    error=type(exc).__name__,
                )

    async def _drain_loser_and_emit_cost(
        self,
        loser_task: asyncio.Task,
        winner_source: str,
    ) -> None:
        """Drain the cancelled loser stream + emit cost-accounting event.

        Tokens consumed by the loser before cancel are billed to the
        tenant. The exact cost can't be observed here (litellm only
        emits cumulative usage on the final chunk, which the loser
        never reaches), so we emit a structlog placeholder + the
        wall-clock ms the loser ran. Operators can correlate with the
        provider's billing dashboard or use that as an upper-bound proxy.
        """
        t0 = time.monotonic()
        try:
            await asyncio.wait_for(loser_task, timeout=DEFAULT_DRAFT_MODEL_TIMEOUT_S)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        except Exception as exc:  # noqa: BLE001 — best-effort drain, must not propagate past the orchestration layer
            logger.debug(
                "speculative_loser_drain_failed",
                error=type(exc).__name__,
            )
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "speculative_loser_cost_usd",
            winner_source=winner_source,
            loser_runtime_ms=elapsed_ms,
            # cost_usd placeholder — exact bill is provider-side. Set
            # 0.0 here so dashboards can sum the field without coalesce.
            cost_usd=0.0,
        )


__all__ = [
    "SPECULATIVE_REDO_SENTINEL",
    "SpeculativeRouter",
    "_aclose_silently",
    "_race_first_token",
]


async def _race_first_token(
    gen: AsyncIterator[str],
) -> tuple[str | None, AsyncIterator[str]]:
    """Module-level alias for ``SpeculativeRouter._race_first_token``.

    Exposed at module level so unit tests can import and exercise the
    helper directly without going through the class.
    """
    return await SpeculativeRouter._race_first_token(gen)
