# Legalbot sysprompt history

## v11 — 2026-05-14 AdapChunk reorg Wave G

**Reason**: Fix HALLU Q24 fabricate ("Khoản 8 Điều 5" bịa)
**Type**: Anti-fabricate-clause rule appended
**Apply**: `plans/260514-adapchunk-reorg-migration/legalbot_sysprompt_v11.sql`

**Test verify**: Re-run scripts/loadtest_legalbot_30q.py → Q24 HALLU breach 0/10.

## v10 — Pre-reorg baseline
1/10 HALLU breach on Q24 (STATE_SNAPSHOT 2026-05-13).

## Earlier versions
See bots_audit_log table for full history.
