"""Vietnamese PII recognizer specs.

Each recognizer is a *spec*: a triple of ``(label, compiled_regex,
description)``. The :class:`RecapPiiDetector` consumes these specs to
collect spans; a future Presidio adapter (see
``plans/260429-PII-presidio-rollout/plan.md``) can register the same
shapes as ``PatternRecognizer`` objects without re-stating the regex
shape.

Domain-neutral guarantee
------------------------
Every pattern keys on a generic structural property (digit count, prefix
``+84``, Vietnamese postal vocabulary like ``Số / Đường / Phường``) —
NEVER on a tenant-specific brand, street, person, bank, or operator
literal. See :file:`docs/dev/SECRET_SCRUB_WORKFLOW.md` for the project's
banned-literal contract.

Patterns source
---------------
The compiled regex bodies all come from :mod:`ragbot.shared.constants`
(``PII_REGEX_*``) per the zero-hardcode rule. Changing a recognizer
requires changing the constant, not the recognizer spec.

Overlap resolution
------------------
The :class:`RecapPiiDetector` uses the ``(start, -length)`` sort already
proven in :mod:`ragbot.infrastructure.pii.vn_regex_pii_redactor` so a
12-digit CCCD always wins over a 9-digit CMND prefix at the same offset,
and a CCCD beginning with ``0`` is NOT mis-classified as a PHONE prefix.

Proof / citation
----------------
- Microsoft Presidio recognizer pattern (open source, MIT):
  https://github.com/microsoft/presidio
  Reference contract for ``(entity_type, regex, score)`` triples.
- RECAP-PII paper: adapts entity-level recognizers to Vietnamese
  national-ID + phone shapes. See
  ``plans/260514-master-of-master/SPRINT-GAP-CLOSURE.md`` for the
  full Vietnamese-specific extension surface.
- VN national-ID regulation (Decree 137/2015/NĐ-CP): CCCD = 12 digits,
  CMND legacy = 9 digits. Encoded in ``PII_REGEX_CCCD`` /
  ``PII_REGEX_CMND``.
- VN telecom prefix list (Circular 22/2014/TT-BTTTT and 2018 renumber):
  mobile = 0[3|5|7|8|9]xxxxxxxx, encoded structurally in
  ``PII_REGEX_PHONE_VN`` as the generic ``0\\d{9,10}`` shape.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ragbot.shared.constants import (
    PII_REGEX_API_KEY_GENERIC,
    PII_REGEX_API_KEY_PROVIDER,
    PII_REGEX_BANK_ACC,
    PII_REGEX_CCCD,
    PII_REGEX_CCCD_SPACED,
    PII_REGEX_CMND,
    PII_REGEX_CREDIT_CARD,
    PII_REGEX_DB_DSN,
    PII_REGEX_EMAIL,
    PII_REGEX_JWT,
    PII_REGEX_PHONE_VN,
    PII_REGEX_PHONE_VN_INTL,
    PII_REGEX_PHONE_VN_SPACED,
    PII_REGEX_VN_ADDRESS,
    PII_REGEX_VN_PLATE,
)


@dataclass(frozen=True)
class VnRecognizerSpec:
    """Single VN PII recognizer spec.

    ``label`` is the canonical entity type emitted in ``found_entities``
    (e.g. ``"CCCD"``, ``"PHONE"``, ``"EMAIL"``). ``pattern`` is the
    pre-compiled regex. ``description`` documents WHY the shape exists
    for security-audit traceability.
    """

    label: str
    pattern: re.Pattern[str]
    description: str


# Order encodes *priority on overlap*: higher-specificity recognizers
# (DSN, JWT, API key shapes, then the 12-digit CCCD) come BEFORE
# lower-specificity ones (CMND 9-digit, PHONE, BANK_ACC, CARD). The
# detector's ``(start, -length)`` sort makes the longer/more-specific
# span win when offsets collide; this list order is a secondary
# tie-breaker via Python's stable sort.
VN_RECOGNIZERS: tuple[VnRecognizerSpec, ...] = (
    VnRecognizerSpec(
        label="DSN",
        pattern=re.compile(PII_REGEX_DB_DSN),
        description=(
            "Database DSN with inline credential — common log/event leak "
            "vector. Drivers covered: postgres / mysql / mongodb / redis "
            "/ amqp."
        ),
    ),
    VnRecognizerSpec(
        label="JWT",
        pattern=re.compile(PII_REGEX_JWT),
        description=(
            "RFC 7519 JWT — three base64url segments. ``eyJ`` prefix is "
            "the stable base64 encoding of ``{\"``."
        ),
    ),
    VnRecognizerSpec(
        label="API_KEY",
        pattern=re.compile(PII_REGEX_API_KEY_PROVIDER),
        description=(
            "Provider-prefixed API keys by SHAPE (sk- / AIza / xox*-). "
            "Matches the published prefix grammar, not a vendor literal."
        ),
    ),
    VnRecognizerSpec(
        label="API_KEY",
        pattern=re.compile(PII_REGEX_API_KEY_GENERIC),
        description=(
            "Generic credential token shape — ``api_<hex>``, "
            "``bearer_<hex>``, etc. Conservative 16-char floor avoids "
            "natural-language false positives."
        ),
    ),
    VnRecognizerSpec(
        label="CCCD",
        pattern=re.compile(PII_REGEX_CCCD),
        description=(
            "VN Căn cước công dân (post-2016) — 12-digit national ID "
            "per Decree 137/2015/NĐ-CP."
        ),
    ),
    VnRecognizerSpec(
        label="CCCD",
        pattern=re.compile(PII_REGEX_CCCD_SPACED),
        description=(
            "VN CCCD pasted with thousand-grouping spaces "
            "(``1234 5678 9012``) — same canonical type."
        ),
    ),
    VnRecognizerSpec(
        label="CARD",
        pattern=re.compile(PII_REGEX_CREDIT_CARD),
        description=(
            "Credit-card-shape 13-19 digits with optional space/dash "
            "separators. Luhn validation lives downstream."
        ),
    ),
    VnRecognizerSpec(
        label="VN_PLATE",
        pattern=re.compile(PII_REGEX_VN_PLATE),
        description=(
            "VN biển số xe — province code + letter series + sequence."
        ),
    ),
    VnRecognizerSpec(
        label="PHONE",
        pattern=re.compile(PII_REGEX_PHONE_VN_INTL),
        description=(
            "VN mobile with international ``+84`` prefix."
        ),
    ),
    VnRecognizerSpec(
        label="PHONE",
        pattern=re.compile(PII_REGEX_PHONE_VN),
        description=(
            "VN mobile prefix ``0[3|5|7|8|9]``, generic 10-11 digit "
            "shape per Circular 22/2014/TT-BTTTT."
        ),
    ),
    VnRecognizerSpec(
        label="PHONE",
        pattern=re.compile(PII_REGEX_PHONE_VN_SPACED),
        description=(
            "VN mobile with human-friendly space / dot / dash "
            "separators (``090 123 4567`` / ``090.123.4567``)."
        ),
    ),
    VnRecognizerSpec(
        label="EMAIL",
        pattern=re.compile(PII_REGEX_EMAIL),
        description="RFC 5322 simplified email shape.",
    ),
    VnRecognizerSpec(
        label="CMND",
        pattern=re.compile(PII_REGEX_CMND),
        description=(
            "VN Chứng minh nhân dân (legacy, phased out 2021 — still "
            "appears in historical docs). 9 digits."
        ),
    ),
    VnRecognizerSpec(
        label="BANK_ACC",
        pattern=re.compile(PII_REGEX_BANK_ACC),
        description=(
            "Generic 10-16 digit bank account number — domain-neutral "
            "(no bank-specific prefix literal)."
        ),
    ),
    VnRecognizerSpec(
        label="VN_ADDRESS",
        pattern=re.compile(PII_REGEX_VN_ADDRESS, re.IGNORECASE),
        description=(
            "VN postal-vocabulary anchored address (Số / Đường / "
            "Phường / Quận / TP). Keyword-anchored so generic Vietnamese "
            "prose isn't falsely masked."
        ),
    ),
)


def get_recognizers() -> tuple[VnRecognizerSpec, ...]:
    """Return the registered VN recognizer specs in priority order.

    Returned tuple is immutable — callers must NOT mutate. Adding a new
    recognizer requires adding a constant in ``shared.constants`` and a
    spec in :data:`VN_RECOGNIZERS`.
    """

    return VN_RECOGNIZERS


def get_recognizer_labels() -> tuple[str, ...]:
    """Return the unique set of canonical labels emitted by the registry.

    Order is the first-seen order in :data:`VN_RECOGNIZERS`. Useful for
    test assertions and dashboard column ordering.
    """

    seen: list[str] = []
    for spec in VN_RECOGNIZERS:
        if spec.label not in seen:
            seen.append(spec.label)
    return tuple(seen)


__all__ = [
    "VN_RECOGNIZERS",
    "VnRecognizerSpec",
    "get_recognizers",
    "get_recognizer_labels",
]
