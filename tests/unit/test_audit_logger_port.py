"""AuditLoggerPort Protocol — concrete + null impls satisfy duck-typing."""

from __future__ import annotations

from ragbot.application.ports.audit_logger_port import AuditLoggerPort
from ragbot.infrastructure.observability.null_audit_logger import NullAuditLogger
from ragbot.infrastructure.observability.pipeline_audit_logger import PipelineAuditLogger


def test_pipeline_audit_logger_is_audit_logger_port():
    """PipelineAuditLogger.log() signature must match AuditLoggerPort.log()."""
    assert isinstance(PipelineAuditLogger(), AuditLoggerPort)


def test_null_audit_logger_is_audit_logger_port():
    """NullAuditLogger acts as default-OFF DI fallback."""
    assert isinstance(NullAuditLogger(), AuditLoggerPort)


def test_build_graph_audit_logger_param_typed_to_port():
    """build_graph kwarg annotation must be `AuditLoggerPort | None`, not `Any | None`."""
    import inspect

    from ragbot.orchestration.query_graph import build_graph

    sig = inspect.signature(build_graph)
    annotation = sig.parameters["audit_logger"].annotation
    rendered = str(annotation)
    assert "AuditLoggerPort" in rendered, rendered
