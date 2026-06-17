"""OpenTelemetry tracing — optional, graceful fallback to no-op.

Enable via OTEL_ENABLED=true + OTEL_EXPORTER_OTLP_ENDPOINT=http://...
If opentelemetry packages not installed, falls back silently to no-op.
"""
from __future__ import annotations

import os
import logging
from contextlib import contextmanager
from typing import Any, Iterator

from ragbot.shared.constants import DEFAULT_SENTRY_SAMPLE_RATE

logger = logging.getLogger(__name__)

_tracer: Any = None
_initialized = False


class _NoOpSpan:
    """Minimal no-op span for when OTel is disabled."""
    def set_attribute(self, key: str, value: Any) -> None: pass
    def set_status(self, *a: Any, **kw: Any) -> None: pass
    def record_exception(self, exc: BaseException) -> None: pass
    def __enter__(self) -> "_NoOpSpan": return self
    def __exit__(self, *a: Any) -> None: pass


class _NoOpTracer:
    """No-op tracer fallback."""

    @contextmanager
    def start_as_current_span(self, name: str, **kw: Any) -> Iterator[_NoOpSpan]:
        yield _NoOpSpan()

    @contextmanager
    def start_span(self, name: str, **kw: Any) -> Iterator[_NoOpSpan]:
        yield _NoOpSpan()


def init_tracing(service_name: str = "ragbot") -> None:
    """Initialize OTel tracing if enabled and packages available."""
    global _tracer, _initialized
    if _initialized:
        return
    _initialized = True

    if not os.getenv("OTEL_ENABLED", "").lower() in ("true", "1", "yes"):
        logger.info("OTel tracing disabled (set OTEL_ENABLED=true to enable)")
        _tracer = _NoOpTracer()
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)

        # OTLP exporter (HTTP or gRPC based on endpoint)
        endpoint = os.getenv(
            "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318",
        )
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
        except ImportError:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )
            exporter = OTLPSpanExporter(endpoint=endpoint)

        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer(service_name)

        # W3C trace context propagation.
        # NB: warn-not-raise here — propagation is a *nice-to-have* on top of
        # local spans. Retrieval / embed / ingest tolerate zero loss when
        # cross-service headers are missing; operator just loses the ability
        # to stitch traces across services. Sacred paths (retrieval/embed)
        # would instead raise loud because correctness depends on them.
        try:
            from opentelemetry.propagate import set_global_textmap
            from opentelemetry.propagators.composite import CompositePropagator
            from opentelemetry.trace.propagation import TraceContextTextMapPropagator
            set_global_textmap(CompositePropagator([TraceContextTextMapPropagator()]))
        except ImportError as exc:
            logger.warning(
                "feature_disabled_dep_missing",
                extra={
                    "module": "tracing",
                    "feature": "w3c_trace_propagation",
                    "missing_pkg": "opentelemetry.propagators",
                    "degraded_to": "no_cross_service_trace_propagation",
                    "error": str(exc)[:100],
                },
            )

        logger.info("OTel tracing initialized", extra={"endpoint": endpoint})
    except ImportError:
        logger.info("OTel packages not installed — using no-op tracer")
        _tracer = _NoOpTracer()
    except Exception as exc:  # noqa: BLE001 — OTel SDK init can raise opaque exception types from grpc/protobuf; downgrade to no-op tracer rather than crash worker.
        logger.warning(
            f"OTel init failed: {exc} — using no-op tracer",
            extra={"error_type": type(exc).__name__},
        )
        _tracer = _NoOpTracer()


def get_tracer(name: str = "ragbot") -> Any:
    """Get the configured tracer (real OTel or no-op)."""
    global _tracer
    if _tracer is None:
        _tracer = _NoOpTracer()
    return _tracer


def init_sentry(dsn: str | None = None) -> None:
    """Initialize Sentry if DSN provided."""
    if not dsn:
        return
    try:
        import sentry_sdk
        sentry_sdk.init(dsn=dsn, traces_sample_rate=DEFAULT_SENTRY_SAMPLE_RATE)
        logger.info("Sentry initialized")
    except ImportError:
        logger.info("sentry-sdk not installed — skipping")


__all__ = ["init_tracing", "init_sentry", "get_tracer"]
