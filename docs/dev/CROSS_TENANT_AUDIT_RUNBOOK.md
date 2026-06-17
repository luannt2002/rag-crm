# Cross-Tenant Audit Runbook

> Audience: backend engineers shipping changes to data-access code.
> Goal: every raw SQL path that touches a tenant-scoped row binds a
> tenant context via `session_with_tenant(...)` so PostgreSQL Row-Level
> Security can enforce isolation. RLS only fires when the connecting
> role is non-superuser; the runtime app DSN points at the
> `ragbot_app` role and the `app.tenant_id` GUC is bound per session.

## Why this matters

`session_with_tenant` issues `SET LOCAL app.tenant_id = '<uuid>'` at the
start of every transaction. RLS policies on tenant-scoped tables read
that GUC and filter rows. A raw `text("...WHERE record_document_id = :id")`
that runs outside this wrapper opens a connection with no GUC bound;
under fail-closed policy semantics that session sees zero rows on
SELECT and affects zero rows on DELETE — but only if the policy is
enabled. Any path that bypasses the wrapper is one missing
`ENABLE ROW LEVEL SECURITY` away from a cross-tenant leak.

The integration test `tests/integration/test_rls_cross_tenant.py`
exercises the policy itself (not the application filter) and asserts
the four invariants: SELECT-bound-A-hides-B, DELETE-on-B-from-A
affects 0 rows, child-table policy inherits parent tenancy, unbound
session sees zero rows.

## Raw SQL paths under audit

The list below was extracted during the cross-tenant validation pass
documented in `reports/260508-validation-of-external-critique.md`,
section "#1 RLS multi-tenant — NUANCED" (sub-section "Real risk
surface", bullet "9+ raw SQL paths"). Every entry has been re-verified
to live inside a `session_with_tenant(...)` block; the wrapper line is
listed for fast spot-check.

| # | Path | Statement | Wrapper |
|---|------|-----------|---------|
| 1 | `src/ragbot/infrastructure/vector/pgvector_store.py:99` | `DELETE FROM document_chunks WHERE record_document_id = :doc_id` | line 95 `session_with_tenant(...)` |
| 2 | `src/ragbot/infrastructure/vector/pgvector_store.py:134` | `DELETE FROM document_chunks WHERE record_document_id = :doc_id` | line 130 `session_with_tenant(...)` |
| 3 | `src/ragbot/application/services/document_service.py:1352` | `SELECT chunk_index, content_hash FROM document_chunks WHERE record_document_id = :doc_id` | line 1348 `session_with_tenant(...)` |
| 4 | `src/ragbot/application/services/document_service.py:1583` | `DELETE FROM document_chunks WHERE record_document_id = :doc_id AND chunk_index >= :min_stale` | line 1577 `session_with_tenant(...)` |
| 5 | `src/ragbot/application/services/document_service.py:1593` | `DELETE FROM document_chunks WHERE record_document_id = :doc_id AND chunk_index = ANY(:indices)` | line 1577 `session_with_tenant(...)` |
| 6 | `src/ragbot/application/services/document_service.py:2122` | `DELETE FROM document_chunks WHERE record_document_id IN (SELECT id FROM documents WHERE record_bot_id = :bid)` | line 2118 `session_with_tenant(...)` |
| 7 | `src/ragbot/application/services/document_service.py:2176` | `DELETE FROM document_chunks WHERE record_document_id = :id` | line 2164 `session_with_tenant(...)` |
| 8 | `src/ragbot/application/services/corpus_version_service.py:222` | `SELECT MAX(...) FROM documents WHERE record_bot_id = :bot_id` | session opened at line 220 (uses `session_factory` + the tenant GUC populated upstream by `tenant_id_ctx`) |

The eighth entry filters on `record_bot_id` rather than
`record_document_id`; the pre-commit guard's pattern targets the
`record_document_id` shape because that is the join key whose owning
tenant cannot be inferred from the parameter alone. `record_bot_id`
sites are reviewed manually — every internal query that joins through
`bots` is implicitly protected once RLS on `bots` is enabled.

## Verification template

To re-audit after a refactor, run from the repo root:

```bash
grep -rnE 'text\(.*WHERE record_document_id' src/ --include='*.py'
bash scripts/precommit_check_raw_sql_tenant_filter.sh
```

The first command lists every raw-SQL site; the second asserts each
site has an enclosing `session_with_tenant(...)` within an 80-line
window above the hit. The pre-commit script exits non-zero on the
first naked site.

For a deeper sweep across all WHERE-clause SQL:

```bash
grep -rnE 'text\(.*WHERE ' src/ --include='*.py' | grep -v session_with_tenant
```

This is noisy (it surfaces `session_with_tenant` call sites themselves
plus admin-only paths that legitimately run as superuser); use it as a
review prompt, not a gate.

## Adding new raw SQL — mandate

When adding a new `text("...")` literal that touches a tenant-scoped
table (`bots`, `documents`, `document_chunks`, `knowledge_edges`,
`semantic_cache`, `request_logs`, `audit_log`, `conversations`, …):

1. **Wrap the call** in `async with session_with_tenant(self._sf,
   record_tenant_id=...) as session:`. Pass the explicit
   `record_tenant_id` keyword from the caller — relying on
   `tenant_id_ctx` is acceptable only when the surrounding service
   contract guarantees the context is set (HTTP middleware path).

2. **Filter on `record_tenant_id` in WHERE** if the table carries the
   column directly. Even with RLS on, defence-in-depth duplication
   surfaces logic bugs early (e.g. an UPDATE that joins to the wrong
   document still 0-rows under RLS but a missing tenant filter
   wouldn't be caught by tests that bypass RLS).

3. **Add a test** that proves cross-tenant isolation. Either extend
   `tests/integration/test_rls_cross_tenant.py` with a new policy-level
   case, or add a unit test that mocks the session and asserts the
   wrapper is called with the expected `record_tenant_id`.

4. **Run the pre-commit guard** before opening the PR:

   ```bash
   bash scripts/precommit_check_raw_sql_tenant_filter.sh
   ```

5. **Update this runbook**: append the new path to the audit table so
   the next reviewer can spot-check without re-deriving the list.

If the new code path genuinely cannot use `session_with_tenant`
(e.g. a one-off ops script running as superuser to seed reference
data), document the escape hatch inline with a `# RLS-bypass: <reason>`
comment and add the path to an exclusion list in the pre-commit script
in the same PR.

## Failure modes the guard catches

- New service method copies a raw SQL pattern from an older path but
  forgets the `session_with_tenant` wrapper.
- Refactor extracts a query into a helper that takes a plain
  `AsyncSession` and the helper is called from a non-tenant-scoped
  caller.
- A migration adds a back-fill loop that runs as the app DSN role
  rather than the admin DSN — RLS would silently 0-row the back-fill.

## Failure modes the guard does NOT catch

- Raw SQL that joins through `record_bot_id` only — covered by RLS on
  `bots` once policy is enabled, but the guard's grep pattern targets
  the `record_document_id` shape. Manual review remains required for
  `record_bot_id` and deeper join keys.
- Code that calls a SQLAlchemy ORM `select(Document).where(...)` —
  ORM queries are tenant-scoped via the same session and the same GUC
  flows through; the guard does not parse ORM AST.
- Reads via Redis / external service that bypass Postgres entirely.

## Related artefacts

- Integration test: `tests/integration/test_rls_cross_tenant.py`
- CI workflow: `.github/workflows/cross-tenant-rls.yml`
- Pre-commit script: `scripts/precommit_check_raw_sql_tenant_filter.sh`
- Engine entry point: `src/ragbot/infrastructure/db/engine.py` —
  `session_with_tenant` definition and `_assert_uuid_str` validator.
- Critique source: `reports/260508-validation-of-external-critique.md`
  section "#1 RLS multi-tenant — NUANCED".
