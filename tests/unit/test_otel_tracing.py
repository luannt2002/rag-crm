"""Tests for OpenTelemetry tracing module (no-op fallback path)."""
import pytest
from ragbot.infrastructure.observability.tracing import (
    _NoOpTracer, _NoOpSpan, get_tracer, init_tracing,
)


class TestNoOpTracer:
    def test_no_op_span_attributes(self):
        span = _NoOpSpan()
        span.set_attribute("key", "value")  # should not raise
        span.record_exception(ValueError("test"))

    def test_no_op_tracer_returns_span(self):
        tracer = _NoOpTracer()
        with tracer.start_as_current_span("test") as span:
            assert span is not None

    def test_no_op_tracer_context_manager(self):
        tracer = _NoOpTracer()
        with tracer.start_span("test") as span:
            span.set_attribute("k", "v")

    def test_get_tracer_returns_something(self):
        tracer = get_tracer()
        assert tracer is not None
        assert hasattr(tracer, "start_as_current_span")

    def test_noop_tracer_span_is_context_manager(self):
        tracer = _NoOpTracer()
        with tracer.start_span("test") as span:
            span.set_attribute("key", "value")
            span.record_exception(ValueError("test"))
        # Should not raise — verify span methods are callable

    def test_init_tracing_without_otel_env(self, monkeypatch):
        monkeypatch.delenv("OTEL_ENABLED", raising=False)
        # Reset state
        import ragbot.infrastructure.observability.tracing as mod
        mod._initialized = False
        mod._tracer = None
        init_tracing()
        tracer = get_tracer()
        assert isinstance(tracer, _NoOpTracer)

    def test_init_tracing_sets_global_tracer(self, monkeypatch):
        import ragbot.infrastructure.observability.tracing as mod
        mod._initialized = False
        mod._tracer = None
        monkeypatch.delenv("OTEL_ENABLED", raising=False)
        init_tracing("test-service")
        tracer = get_tracer()
        # After init without OTEL_ENABLED, should be NoOpTracer
        assert isinstance(tracer, _NoOpTracer)
        # But should be the SAME instance (singleton)
        assert get_tracer() is tracer
