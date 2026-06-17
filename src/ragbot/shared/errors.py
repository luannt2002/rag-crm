"""Exception hierarchy.

Ref: docs/application/PLAN_02_CONVENTIONS_BASE_CONTRACTS.md §errors.py
     RAGBOT_MASTER §26.4.

Three branches:
- DomainError         — business invariant violated.
- ApplicationError    — orchestration / use case / policy failures.
- InfrastructureError — adapter / external service failures.

Each exception has a stable `code` (UPPER_SNAKE) used in API error envelope.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ErrorInfo:
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


class RagbotError(Exception):
    """Base class for all RAGbot errors."""

    code: str = "RAGBOT_ERROR"
    http_status: int = 500

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.__class__.__doc__ or self.code
        self.details: dict[str, Any] = details or {}
        super().__init__(self.message)

    def to_envelope(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


# ============================================================================
# Domain errors
# ============================================================================
class DomainError(RagbotError):
    """Business invariant violation."""

    code = "DOMAIN_ERROR"
    http_status = 400


class TenantIsolationViolation(DomainError):
    """Attempt to access / modify data outside tenant scope."""

    code = "TENANT_ISOLATION_VIOLATION"
    http_status = 403


class CitationHallucinated(DomainError):
    """LLM produced a citation not present in retrieved set."""

    code = "CITATION_HALLUCINATED"
    http_status = 500  # hide details from caller


class QuotaExceeded(DomainError):
    """Tenant has exhausted monthly quota."""

    code = "QUOTA_EXCEEDED"
    http_status = 429


class InvalidDocumentState(DomainError):
    """Invalid state transition for Document aggregate."""

    code = "INVALID_DOCUMENT_STATE"
    http_status = 409


class DocumentDuplicateError(DomainError):
    """A document with the same raw_content (sha256) already exists for this bot."""

    code = "DOCUMENT_DUPLICATE"
    http_status = 409


class ModerationRejected(DomainError):
    """Input or output blocked by moderation."""

    code = "MODERATION_REJECTED"
    http_status = 400


class PromptInjectionDetected(DomainError):
    """Detected prompt injection signal (canary leak, regex match, classifier)."""

    code = "PROMPT_INJECTION_DETECTED"
    http_status = 400


class SourceNotAllowedError(DomainError):
    """source_url failed the per-bot source allow-list (PoisonedRAG defence).

    Raised at the ingest boundary when ``source_allowlist_enabled`` is
    True and the inbound document's ``source_url`` does not match any
    pattern in ``bots.plan_limits.allowed_source_domains``. The
    ``reason`` attribute carries the matcher's snake_case reject code
    (e.g. ``"domain_not_in_allowlist"``) so callers / audit can branch
    without parsing the message.
    """

    code = "SOURCE_NOT_ALLOWED"
    http_status = 422


class InvariantViolation(DomainError):
    """Generic domain invariant violated."""

    code = "INVARIANT_VIOLATION"
    http_status = 422


# ============================================================================
# Application errors
# ============================================================================
class ApplicationError(RagbotError):
    """Orchestration / use case failure."""

    code = "APPLICATION_ERROR"
    http_status = 500


class UseCaseError(ApplicationError):
    code = "USE_CASE_ERROR"
    http_status = 500


class PolicyViolation(ApplicationError):
    code = "POLICY_VIOLATION"
    http_status = 403


class IdempotencyConflict(ApplicationError):
    code = "IDEMPOTENCY_CONFLICT"
    http_status = 409


class JobNotFound(ApplicationError):
    code = "JOB_NOT_FOUND"
    http_status = 404


class UnauthorizedError(ApplicationError):
    code = "UNAUTHORIZED"
    http_status = 401


class ForbiddenError(ApplicationError):
    code = "FORBIDDEN"
    http_status = 403


class GraphAssemblyError(ApplicationError):
    """A required graph DI dependency failed to resolve from the container."""

    code = "GRAPH_ASSEMBLY_ERROR"
    http_status = 503


# ============================================================================
# Infrastructure errors
# ============================================================================
class InfrastructureError(RagbotError):
    """External adapter / infrastructure failure."""

    code = "INFRASTRUCTURE_ERROR"
    http_status = 503


class RepositoryError(InfrastructureError):
    code = "REPOSITORY_ERROR"


class VectorStoreError(InfrastructureError):
    code = "VECTOR_STORE_ERROR"


class LLMError(InfrastructureError):
    code = "LLM_ERROR"


class CacheError(InfrastructureError):
    code = "CACHE_ERROR"


class BusError(InfrastructureError):
    code = "BUS_ERROR"


class InboxDuplicateError(BusError):
    """Inbox mark hit an existing (subscriber_id, msg_id) row.

    Raised inside the handler's transaction by the ``inbox_tx`` hook so
    a concurrent duplicate delivery rolls back its side-effects instead
    of double-applying — the PK-conflict-aborts-the-tx idempotent
    consumer pattern. The bus treats it as "already processed
    elsewhere": no XACK, a later redelivery skips from the committed
    inbox row.
    """

    code = "BUS_INBOX_DUPLICATE"


class ExternalServiceError(InfrastructureError):
    code = "EXTERNAL_SERVICE_ERROR"


class CircuitBreakerOpen(InfrastructureError):
    code = "CIRCUIT_BREAKER_OPEN"
    http_status = 503


# ============================================================================
# narrow exception classes for broad-except sweep (P20 4-layer
# audit lesson). Use these instead of `except Exception` in:
#   - audit / observability writes (AuditEmitError)
#   - retrieval pipeline failures (RetrievalError)
#   - embedding adapter failures (EmbeddingError)
#   - ingest pipeline failures (IngestError)
# ============================================================================
class AuditEmitError(InfrastructureError):
    """Audit / observability write failed (request_log, step, pipeline_audit)."""

    code = "AUDIT_EMIT_ERROR"
    http_status = 500


class RetrievalError(InfrastructureError):
    """Retrieval pipeline failed (vector / hybrid / rerank / fusion)."""

    code = "RETRIEVAL_ERROR"
    http_status = 503


class EmbeddingError(InfrastructureError):
    """Embedding adapter failed after retry budget exhausted."""

    code = "EMBEDDING_ERROR"
    http_status = 503


class IngestError(InfrastructureError):
    """Document ingest pipeline failed (chunk / embed / persist)."""

    code = "INGEST_ERROR"
    http_status = 500


class WorkspaceIdInvalid(InfrastructureError):
    """workspace_id failed length / format validation. → HTTP 422."""

    code = "WORKSPACE_ID_INVALID"
    http_status = 422


class KeyVerifyError(ApplicationError):
    """API key verify call failed (4xx from provider API)."""

    code = "KEY_VERIFY_ERROR"
    http_status = 400


class KeyNotFoundError(ApplicationError):
    """ai_keys row not found by id."""

    code = "KEY_NOT_FOUND"
    http_status = 404


__all__ = [
    "ApplicationError",
    "AuditEmitError",
    "BusError",
    "InboxDuplicateError",
    "CacheError",
    "CircuitBreakerOpen",
    "CitationHallucinated",
    "DocumentDuplicateError",
    "DomainError",
    "EmbeddingError",
    "ErrorInfo",
    "ExternalServiceError",
    "ForbiddenError",
    "GraphAssemblyError",
    "IdempotencyConflict",
    "IngestError",
    "KeyNotFoundError",
    "KeyVerifyError",
    "InfrastructureError",
    "InvalidDocumentState",
    "InvariantViolation",
    "JobNotFound",
    "LLMError",
    "ModerationRejected",
    "PolicyViolation",
    "PromptInjectionDetected",
    "QuotaExceeded",
    "RagbotError",
    "RepositoryError",
    "RetrievalError",
    "TenantIsolationViolation",
    "UnauthorizedError",
    "UseCaseError",
    "VectorStoreError",
    "WorkspaceIdInvalid",
]
