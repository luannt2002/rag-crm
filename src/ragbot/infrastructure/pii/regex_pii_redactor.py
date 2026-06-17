"""Regex-based PII redactor (VN + EN baseline).

Production should swap with `presidio-analyzer` / `presidio-anonymizer`
behind the same `PIIRedactorPort` interface.
"""

from __future__ import annotations

import re

from ragbot.application.ports.pii_port import PIIRedactorPort

_PATTERNS: dict[str, re.Pattern[str]] = {
    "EMAIL": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "PHONE_VN": re.compile(r"(?:\+?84|0)(?:3[2-9]|5[2689]|7[06-9]|8[0-9]|9[0-9])\d{7}"),
    "CCCD_VN": re.compile(r"\b\d{12}\b"),
    "BANK_ACCT": re.compile(r"\b\d{10,16}\b"),
    "IP": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
}


class RegexPIIRedactor(PIIRedactorPort):
    async def redact(
        self,
        text: str,
        *,
        language: str = "vi",  # noqa: ARG002
    ) -> tuple[str, dict[str, list[tuple[int, int]]]]:
        spans: dict[str, list[tuple[int, int]]] = {}
        out = text
        # Replace longest first to avoid overlap issues
        for kind, pat in _PATTERNS.items():
            kind_spans: list[tuple[int, int]] = []
            for m in pat.finditer(out):
                kind_spans.append(m.span())
            if kind_spans:
                spans[kind] = kind_spans
            out = pat.sub(f"[REDACTED_{kind}]", out)
        return out, spans


__all__ = ["RegexPIIRedactor"]
