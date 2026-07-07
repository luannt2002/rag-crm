# Domain-neutral betrayal audit — VERIFIED (workflow 40 agents, 2026-07-07)

19 CONFIRMED / 32 raw. by_type={'price-domain-coupling': 11, 'lang-vocab-hardcode': 6, 'rigid-schema-guess': 1, 'domain-assumption-in-prompt': 1} by_sev={'high': 4, 'medium': 14, 'low': 1}

## F1 price-first-class (=ADR-0007) — 11 findings
- [HIGH] src/ragbot/shared/document_stats.py:325 — ParsedEntity.price_primary / price_secondary
    REMOVE→REPLACE: Replace price_primary/price_secondary with a domain-neutral typed-value model: e.g. values: list[LabelledValue(label, raw, numeric, unit)] where unit/label come from the header, not a hardcoded currency. Rank/dedup on generic numeric roles, not 'price'.
- [HIGH] src/ragbot/shared/document_stats.py:306 — ParsedEntity.price_primary / price_secondary + _column_roles PRICE role + parse_
    REMOVE→REPLACE: Replace price_primary/secondary + PRICE role with a generic values: list[LabelledValue] keyed by column header, where a 'value cell' is any number+unit shape (unit read from the corpus cell, not hardcoded), and the min/max floor is a per-bot/per-locale config 
- [HIGH] src/ragbot/shared/document_stats.py:323 — ParsedEntity.price_primary / price_secondary
    REMOVE→REPLACE: Replace price_primary/price_secondary with a generic typed-measure list: measures: list[{label, value, unit}] where unit/label come from the corpus header (not hardcoded). Rank/range/count over (label,unit) tuples so any domain's numbers are queryable the same
- [HIGH] src/ragbot/infrastructure/repositories/stats_index_repository.py:269 — query_by_price_range / top_by_price / count_by_price_range / _price_clauses / pr
    REMOVE→REPLACE: Store measures in a typed side-table (entity_id, label, unit, value NUMERIC) and make range/top/count generic over (label,unit). Query route selects the measure label from the parsed question rather than a fixed price_column enum. Rename document_service_index
- [MEDIUM] src/ragbot/shared/document_stats.py:279 — parse_money_vn + DEFAULT_PRICE_MIN_VND / DEFAULT_PRICE_MAX_VND / DEFAULT_PRICE_B
    REMOVE→REPLACE: Replace with a currency/unit-neutral numeric parser that extracts a raw number + captures the unit/currency token from the cell or column header (config-sourced, not baked VND). Floor/buckets must be per-unit config, not a single VND constant.
- [MEDIUM] src/ragbot/application/services/document_service/ingest_stages_final.py:138 — _dedup_stats_entities / _entity_richness
    REMOVE→REPLACE: Key dedup on (name, tuple(neutral value cells)) or a content hash of the non-name value columns, and rank richness by count of populated value/attribute cells generally — not by presence of a price field specifically.
- [MEDIUM] src/ragbot/shared/tabular_markdown.py:203 — _normalize_rows forward-fill seeded on _has_money
    REMOVE→REPLACE: Seed forward-fill and DATA detection on a generic 'row carries a value cell' predicate (any number/unit or non-label value), not _has_money; treat money as one instance of a value, so non-money tables get the same merged-cell recovery and data/header split.
- [MEDIUM] src/ragbot/shared/document_stats.py:708 — _extract_entity_from_row (pure-money → price fallback)
    REMOVE→REPLACE: Do not default-cast unroled numeric cells to 'price'. Store them as generic labelled measures keyed by their column header; only route to a price-typed field when the column role is explicitly price (owner-declared or a currency-unit token detected in the cell
- [MEDIUM] src/ragbot/shared/constants/_21_streaming_upload.py:73 — DEFAULT_PRICE_MIN_VND
    REMOVE→REPLACE: Replace the fixed VND floor with a currency/scale-neutral policy: either no floor (rely on the digit-count + column-role signal) or a per-bot/per-locale floor resolved from config keyed on the bot's declared currency/scale, defaulting to 0 (disabled).
- [MEDIUM] src/ragbot/shared/constants/_21_streaming_upload.py:66 — DEFAULT_PRICE_BUCKETS_VND
    REMOVE→REPLACE: Make the default buckets currency/scale-neutral — derive bucket edges at runtime from the observed value distribution (quantiles of the ingested numeric column) rather than fixed VND literals, keeping system_config as an explicit override.
- [MEDIUM] src/ragbot/shared/query_range_parser.py:97 — RangeFilter.price_min/price_max/price_column
    REMOVE→REPLACE: Rename to a domain-neutral numeric-range schema (e.g. value_min/value_max/value_column, targeting a generic numeric attribute role resolved from the corpus) so any labelled numeric column — not just 'price' — can be range-filtered/ranked.

## F2 lang-vocab hardcode (→locale packs) — 6 findings
- [LOW] src/ragbot/shared/document_stats.py:220 — _AGGREGATE_TOKENS
    REMOVE→REPLACE: Move aggregate-label tokens into the same per-locale language pack as the role tokens (finding 2); or detect total rows structurally (a row whose value equals the column sum) rather than by label vocabulary.
- [MEDIUM] src/ragbot/shared/document_stats.py:188 — _PRICE_COL_TOKENS / _NAME_COL_TOKENS / _CATEGORY_COL_TOKENS / _ALIASES_COL_TOKEN
    REMOVE→REPLACE: Move role-token vocab into per-locale language packs keyed by the doc/bot language code (like other language_packs content), and thread the detected locale into _column_roles; keep detection primarily structural (label-shape + value-contrast, already present) 
- [MEDIUM] src/ragbot/shared/document_stats.py:174 — _NAME_COL_TOKENS / _CATEGORY_COL_TOKENS / _PRICE_COL_TOKENS / _ALIASES_COL_TOKEN
    REMOVE→REPLACE: Move the four role-token sets into per-locale packs keyed by language code (mirror DEFAULT_STATS_DISCOURSE_OPENERS_BY_LANG) and thread the doc/bot language into _column_roles, so a new language ships as a config pack, not a code edit. Keep the structural separ
- [MEDIUM] src/ragbot/shared/number_format.py:48 — _SUFFIX_MULT
    REMOVE→REPLACE: Move the suffix→multiplier map into the per-locale language pack (like RoutingSignals already does for below/above tokens); parse_money_vn should take a resolved suffix table for the bot's locale, with the vi map as the default seed only.
- [MEDIUM] src/ragbot/shared/query_range_parser.py:158 — _RANGE_FROM_TO_RE / _FUZZY_RE / _ANY_MONEY_RE
    REMOVE→REPLACE: Build the unit alternation from the locale pack's suffix keys (same source as number_format._SUFFIX_MULT) instead of hardcoding vi units in the regexes; compile per-locale.
- [MEDIUM] src/ragbot/shared/constants/_02_per_intent_rerank_skip_gate_.py:233 — SUPERLATIVE_SUPPORTED_LANGUAGES
    REMOVE→REPLACE: Drive superlative support off the presence of superlative tokens in the bot's locale pack (empty pack → naturally no-op) instead of a static language allowlist in constants.

## F3 rigid-schema guess (=ADR-0008) — 1 findings
- [MEDIUM] src/ragbot/shared/document_stats.py:174 — _NAME_COL_TOKENS / _CATEGORY_COL_TOKENS / _PRICE_COL_TOKENS / _ALIASES_COL_TOKEN
    REMOVE→REPLACE: Demote the VN/EN token sets to an OPTIONAL per-locale role pack (config-keyed by language) plus owner custom_roles; make the neutral primary path positional/structural (name = first non-value label column, value = any number+unit column) so a non-VN/EN bot bin

## F3b domain-assumption — 1 findings
- [MEDIUM] src/ragbot/orchestration/nodes/generate.py:283 — _detected_service = (_new_slots or {}).get("service")
    REMOVE→REPLACE: Remove the hardcoded "service" key. Let the owner declare which slot is the lock entity in action_config (e.g. action_config['lock_slot']), and read _detected_service = _new_slots.get(action_cfg.get('lock_slot')) with graceful skip when unset.
