"""PresidioPiiRedactor — STUB Strategy for Microsoft Presidio.

Production wiring requires:
  pip install presidio-analyzer presidio-anonymizer
  + spaCy VN model (vi_core_news_lg)  ~ 500 MB

Default OFF. Constructor raises :class:`NotImplementedError` so the
registry's fail-soft path falls back to NullPiiRedactor.
"""

from __future__ import annotations


class PresidioPiiRedactor:
    """Presidio PII redactor stub — raises until deps are installed."""

    def __init__(self, **_: object) -> None:
        raise NotImplementedError(
            "Presidio PII redactor requires `presidio-analyzer` + "
            "`presidio-anonymizer` (see "
            "plans/260429-PII-presidio-rollout/plan.md). Default OFF — "
            "use the `vn_regex` provider for cheap VN-focused redaction."
        )

    @staticmethod
    def get_provider_name() -> str:
        return "presidio"

    def redact(self, text: str) -> tuple[str, list[dict]]:  # pragma: no cover  # noqa: ARG002
        raise NotImplementedError


__all__ = ["PresidioPiiRedactor"]
