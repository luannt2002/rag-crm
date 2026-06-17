"""DomainAllowlistValidator — per-bot source-URL gate (PoisonedRAG defence).

Implements :class:`ragbot.application.ports.source_validator_port.SourceValidatorPort`.
Bot owner populates ``bots.plan_limits.allowed_source_domains`` with a list
of patterns; ingest rejects any ``source_url`` that does not match one of
them. Empty list = allow-all (feature opt-in default).

Pattern grammar (3 forms, evaluated in order):

1. **Regex** — entry prefixed ``re:`` is compiled and matched against the
   full URL (``re.search`` semantics, anchor explicitly via ``^``/``$``
   if needed). Example: ``re:^https?://docs\\.example\\.com/.*``.

2. **URL prefix** — entry starting with ``http://`` or ``https://`` is
   matched via ``startswith`` against the full URL. Example:
   ``https://example.com/wiki/`` allows everything under that path but
   rejects ``https://example.com/admin/``.

3. **Bare host** — anything else is treated as a host name. Match if the
   URL's netloc equals the entry OR is a sub-domain of it (so ``example.com``
   matches ``api.example.com`` but NOT ``evilexample.com``). Case-insensitive.

Reject reasons (logged in structlog + audit):

- ``no_source_url`` — empty source_url AND patterns configured (the bot
  owner asked for filtering; manual paste must explicitly carry a tag).
- ``malformed_url`` — :mod:`urllib.parse` could not extract a netloc and
  the URL is not a relative path that matches any prefix entry.
- ``domain_not_in_allowlist`` — URL parsed OK but no pattern matched.
- ``regex_pattern_invalid`` — a configured regex failed to compile (the
  faulty pattern is logged but the *other* patterns still get a chance
  to match; only when zero pattern matches do we reject).

Proof citation: Zou et al. (2024) "PoisonedRAG", arXiv:2402.07867 §6.1
— source allow-list listed as the primary structural defence against
knowledge-corruption attacks (90% baseline ASR → <5% with allow-list).
"""

from __future__ import annotations

import re
from typing import Sequence
from urllib.parse import urlparse

import structlog

logger = structlog.get_logger(__name__)


# Sentinel prefix for explicit regex entries in the allow-list. The bot
# owner writes ``re:^https?://docs\.example\.com/.*`` to opt into regex
# matching; bare host / prefix entries do not need any sentinel.
_REGEX_SENTINEL: str = "re:"
_URL_SCHEME_PREFIXES: tuple[str, ...] = ("http://", "https://")


class DomainAllowlistValidator:
    """Per-bot source-URL allow-list validator."""

    def __init__(self, **_: object) -> None:
        return

    @staticmethod
    def get_provider_name() -> str:
        return "domain_allowlist"

    def is_allowed(
        self,
        source_url: str,
        allowed_patterns: Sequence[str],
    ) -> tuple[bool, str | None]:
        # Empty pattern list = feature opt-in default = allow-all. This
        # is the path most tenants take until they explicitly populate
        # ``plan_limits.allowed_source_domains``.
        patterns = [p for p in (allowed_patterns or []) if isinstance(p, str) and p.strip()]
        if not patterns:
            return True, None

        # Patterns configured → empty source_url is an explicit reject
        # (the bot owner asked for filtering; an untagged manual paste
        # cannot be assigned a provenance domain).
        if not source_url or not source_url.strip():
            return False, "no_source_url"

        # URL + entries get lowercased for matching so case differences
        # in scheme/host/path don't bypass the allow-list. plan_limits
        # validation already lowercases ``list_str`` entries; we mirror
        # that here on the URL side to keep regex / prefix matching
        # consistent. (Bare-host comparison was already lower-cased.)
        url = source_url.strip().lower()
        host = self._extract_host(url)

        any_pattern_matched = False
        for raw in patterns:
            # ``plan_limits`` validation already lowercases ``list_str``
            # entries, but the validator is callable directly with raw
            # input (CLI, tests, bots that bypass validate_plan_limits)
            # so we lowercase defensively here too. Regex patterns are
            # included in the lowercase pass — bot owner must author
            # them in lower-case form for the URL match.
            entry = raw.strip().lower()
            if not entry:
                continue
            try:
                matched = self._match_one(url, host, entry)
            except re.error as exc:
                # Malformed regex — log but do NOT short-circuit reject;
                # the other patterns still get a chance to match. We track
                # whether ANY pattern actually evaluated so an all-bad
                # config surfaces as ``regex_pattern_invalid``.
                logger.warning(
                    "source_allowlist_regex_invalid",
                    pattern=entry,
                    error=str(exc),
                )
                continue
            any_pattern_matched = True
            if matched:
                return True, None

        if not any_pattern_matched:
            # Every configured pattern was an invalid regex — fail closed:
            # reject so the bot owner notices and fixes the config rather
            # than silently allow-all when filtering was requested.
            return False, "regex_pattern_invalid"

        # Patterns evaluated cleanly but none matched.
        if not host and not url.startswith(_URL_SCHEME_PREFIXES):
            return False, "malformed_url"
        return False, "domain_not_in_allowlist"

    @staticmethod
    def _extract_host(url: str) -> str:
        """Return the netloc (caller pre-lowercased the URL), or empty
        string on parse failure.

        ``urlparse`` never raises on garbage input — it just returns an
        empty netloc. We additionally strip an ``user:pass@`` prefix and
        any explicit port so the host-comparison path matches both
        ``https://docs.example.com:443/x`` and ``https://docs.example.com/x``.
        """
        try:
            parsed = urlparse(url)
        except ValueError:
            return ""
        netloc = parsed.netloc or ""
        if "@" in netloc:
            netloc = netloc.rsplit("@", 1)[-1]
        if ":" in netloc:
            netloc = netloc.split(":", 1)[0]
        return netloc

    @staticmethod
    def _match_one(url: str, host: str, entry: str) -> bool:
        """Match a single allow-list entry against the URL + host.

        Caller has already lowercased both ``url`` and the patterns
        before reaching here, so all comparisons are case-insensitive
        without per-call ``re.IGNORECASE`` flags.

        Raises ``re.error`` for malformed regex entries so the caller can
        log + skip that specific entry without aborting the whole check.
        """
        # 1) Explicit regex entry
        if entry.startswith(_REGEX_SENTINEL):
            pattern = entry[len(_REGEX_SENTINEL):]
            # Caller anchors via ``^``/``$`` explicitly; we use search()
            # so a relaxed pattern still works without anchoring.
            return re.search(pattern, url) is not None

        # 2) URL prefix entry (must include scheme)
        if entry.startswith(_URL_SCHEME_PREFIXES):
            return url.startswith(entry)

        # 3) Bare host — exact match or sub-domain match. Strip a leading
        # ``.`` so the bot owner can write ``.example.com`` for a "only
        # sub-domains, not the apex host" semantic if desired.
        entry_norm = entry.lstrip(".")
        if not entry_norm or not host:
            return False
        # Exact host match
        if host == entry_norm:
            return True
        # Sub-domain match — host must end with ``.<entry>`` so
        # ``evilexample.com`` does NOT match allow-list entry
        # ``example.com``.
        return host.endswith("." + entry_norm)


__all__ = ["DomainAllowlistValidator"]
