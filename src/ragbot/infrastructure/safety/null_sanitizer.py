"""NullSanitizer — passthrough Strategy default for the CleanBase Port.

Used whenever ``system_config.cleanbase_tier0_enabled`` is ``False`` so
ingest callers wire ``sanitizer.sanitize(content)`` unconditionally and
still pay zero cost. The returned report carries zero counts so existing
telemetry pipelines (e.g. dashboards aggregating ``total_redactions``)
keep producing well-typed rows.
"""

from __future__ import annotations

from ragbot.application.ports.sanitizer_port import SanitizeReport


class NullSanitizer:
    """No-op sanitizer — :meth:`sanitize` returns input + zero-count report."""

    def __init__(self, **_: object) -> None:
        return

    @staticmethod
    def get_provider_name() -> str:
        return "null"

    def sanitize(self, text: str) -> tuple[str, SanitizeReport]:
        """Return ``text`` verbatim with a zero-count :class:`SanitizeReport`.

        Empty / non-string-like input is accepted defensively so callers
        do not need to wrap every call site in a None check.
        """
        if not isinstance(text, str):
            text = "" if text is None else str(text)
        return text, SanitizeReport(
            provider_name="null",
            n_chars_in=len(text),
            n_chars_out=len(text),
            html_tags_stripped=0,
            zero_width_removed=0,
            injection_patterns_matched=0,
            nfc_changed=False,
        )


__all__ = ["NullSanitizer"]
