# [T3-Refactor] Ingest flow — clean-code review · comment standardization · dead-code report

> Scope: the 22 files of the document INGEST/UPLOAD flow (audited 2026-06-23).
> Goal: (1) document the latest flow, (2) assess clean-code / OOP / DI / pattern health,
> (3) standardize comments to full English (WHY-only, no temporal/version refs — CLAUDE.md),
> (4) detect + isolate unused functions into a dedicated report.
> Tier: T3-Refactor (no behavior change) — must NOT touch logic, only comments/docstrings.

## Constraints (sacred)
- **No behavior change** — comment/docstring edits only; every code line stays byte-identical.
- **No-version-ref**: comments describe WHY/contract, never Sprint/Bug-####/date/`260525`/`v2`.
- **English-only** docstrings + comments in these files.
- **Never comment-out a framework-registered handler or a Port/Protocol method** as "unused"
  (would break the app / the interface). Verify decorator + interface before flagging.
- Surgical: one file at a time; full unit suite green after each batch.

## Phases
- **P1 — Flow + assessment (DONE this session)**: `docs/dev/INGEST_FLOW_LATEST.md` (latest flow +
  per-file design-pattern/clean-code verdict) + `reports/INGEST_UNUSED_FUNCS_20260623.md` (verified
  dead-code report). New files only — zero code risk.
- **P2 — Comment standardization (per file)**: translate VN→EN, strip temporal/version refs, add
  module + function docstrings (purpose + contract + WHY). Order: entry (documents/sync) → worker →
  service (ingest_core/stages) → parsers → ocr → shared. Run `pytest` after each batch.
- **P3 — Verify**: ruff on touched files = 0 new; full unit suite green; grep guard temporal/version = 0.

## Comment standard (applied in P2)
- **Module docstring**: 1-3 lines — what this module is responsible for + its place in the flow.
- **Class docstring**: the role (adapter/service/port-impl) + the pattern it realizes (Strategy/Registry/Null).
- **Function docstring**: purpose; `Args:`/`Returns:`/`Raises:` when non-obvious; a WHY line for any
  non-obvious decision. NO "260525", NO "Sprint", NO "Bug #", NO "v2/legacy/new", NO Vietnamese.
- **Inline `#`**: only for non-obvious WHY (a guard, an ordering constraint, a workaround root-cause).

## Status
- P1: ✅ done (this session).
- P2: ⏳ pending — execute file-by-file on approval / autonomous continue.
- P3: ⏳ pending.
