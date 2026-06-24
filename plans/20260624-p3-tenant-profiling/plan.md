# [T1-Smartness] P3 Tenant-Profiling — per-bot ingest STYLE meta-rules (domain-neutral)

> Pain P3 (rag-painpoints): owner uses a non-standard heading/table convention (bold/uppercase line as
> heading without `#`; `;`-separated columns) → the GLOBAL rule-based block-detect
> (`shared/chunking/analyze.py::_is_heading_line` / `_is_table_line`) mis-fires → wrong block/table
> detection → wrong chunking. Status today: 🔴 OPEN, rules are global.

## Expert layer (painpoints CORE MINDSET — normalize BEFORE chunking)
Do NOT make every global detection rule config-aware (invasive, touches all call sites). Instead, apply a
per-bot **style normalizer** at pre-process that PROMOTES the owner's convention into the canonical
markdown the global rules already understand (`## `, `| a | b |`). Then AdapChunk/analyze work unchanged.
"Ép định dạng bẩn về chuẩn nghèo ở tầng tiền-xử-lý TRƯỚC."

## Reuse the proven per-bot config pattern (zero new subsystem)
Per-bot config already flows via `plan_limits.chunking_config` → `resolve_chunking_policy()` (pure helper,
self-validating, drops-invalid) → consumed at `_stage_u4_chunk` (ingest_stages.py:394). Add a `style_profile`
sub-dict to that SAME chain. Defaults = no-op → existing bots byte-identical (opt-in). Re-ingest applies.

### Profile keys (domain-neutral, opt-in, default OFF)
`plan_limits.chunking_config.style_profile = {`
- `heading_uppercase_promote: bool` (default False) — a standalone ALL-CAPS short line (within
  `DEFAULT_TOPIC_UPPER_SECTION_MIN/MAX_CHARS`, not already heading/table/list) → prefix `## `.
- `table_separator: str` (default "") — a single owner separator char (e.g. `;` / `~`); a line with ≥2 of
  it (≥3 cells) and no sentence punctuation → rewritten to a `| a | b | c |` pipe row.
`}`  (more keys later = add to the resolver + normalizer; same pattern.)

## Stages
### Stage 1 — CODE (main session, TDD)
1. `shared/chunking/tenant_style.py` (NEW) — pure `apply_tenant_style(text, *, heading_uppercase_promote,
   table_separator) -> str`. Line-by-line; only promotes lines that are unambiguous (guards mirror the
   CSV carve-outs: skip lines already heading/table/pipe; require ≥2 separators; exclude `". "`, trailing
   `.`/`;`/`:`). No-op when both knobs default. Domain-neutral (no brand). Bounds from constants.
2. `shared/chunking_policy.py` — extend `resolve_chunking_policy` to also resolve+validate a
   `style_profile` dict (per-bot `chunking_config.style_profile` > platform > {}); validate: bool coerced,
   `table_separator` must be a single non-alphanumeric char not in `{| # * ` }` else "".
3. `ingest_stages.py::_stage_u4_chunk` — after `_policy` resolved, if `style_profile` active, apply
   `apply_tenant_style(content, ...)` to `content` BEFORE `analyze_document` + `smart_chunk`. Log a
   structured `tenant_style_applied` event (lines promoted) when active.
4. TDD FIRST: `tests/unit/test_tenant_style.py` — uppercase line → `## `; `;`-row → pipe row; prose with
   one `;` untouched; existing heading/table untouched; both-default = identity (byte-equal). Plus
   `test_chunking_policy.py` extension: style_profile resolved + invalid separator dropped.
5. Verify: ruff HEAD==NOW · domain-neutral grep 0 · zero-hardcode (bounds from constants).

### Stage 2 — DOC
`docs/dev/INPUT_DATA_CONTROL_FLOW_DESIGN.md` + rag-painpoints P3 status 🔴→🟡: document the style_profile
keys + that it is opt-in per-bot, re-ingest required, normalize-before-chunk layer.

## Verification gate
TDD green · ruff 0-new · domain-neutral grep 0 · default-OFF identity proven (existing bots unaffected) ·
no per-bot literal in core (reads bot_id config, no `if bot_id==`). Live re-ingest A/B = DEFER (needs an
owner doc with the non-standard convention; note in skipped).
