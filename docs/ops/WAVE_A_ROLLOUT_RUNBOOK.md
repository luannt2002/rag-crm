# Wave A Rollout Runbook

3 operator migration scripts unblock Wave G defer items. Run **in this
order**; each phase is independently reversible.

## Phase 1 — Rotate ANTHROPIC_API_KEY (unblocks BF2 CR pilot)

**Why**: Wave G BF2 contextual-retrieval pilot blocked on `401` from
Anthropic Haiku endpoint. New key staged out-of-band.

```bash
# 1a. Stage NEW key in shell env (NOT in .env yet)
export ANTHROPIC_API_KEY_NEW="<paste-new>"

# 1b. Pre-flight (1-token Haiku probe — does NOT touch .env)
python scripts/ops_anthropic_key_rotate.py --action preflight
# Expect: "[preflight] NEW key OK: ok (id=msg_...)"

# 1c. Atomic rotate (writes .env.backup-<TS>, restarts document-worker, smoke)
python scripts/ops_anthropic_key_rotate.py --action rotate

# 1d. Rollback if anything misbehaves within 24h
python scripts/ops_anthropic_key_rotate.py --action rollback
# Picks newest .env.backup-* automatically; --backup-path overrides.
```

**Verify**: BF2 pilot harness re-runs cleanly:
`python scripts/wave_g_cr_reingest_pilot.py --turns 50` (no 401 logged).

**Cleanup**: 24h after `rotate`, `rm .env.backup-<TS>` (keys retained in
backup file are still valid — keep until rotation is confirmed stable).

## Phase 2 — Enable Cascade Routing per-bot (BF1 CONDITIONAL ENABLE)

**Why**: Wave G BF1 verdict CONDITIONAL ENABLE — Haiku tier unlocks
21H/29M/37S with -4.26% cost. Roll out per-bot (start `test-spa-id`).

```bash
# 2a. Pre-flight tier rows + bot existence
python scripts/ops_cascade_enable_per_bot.py \
    --bot test-spa-id --workspace dev-ws --channel web \
    --action preflight
# Expect: tier rows OK, bot found, current flag printed.

# 2b. Enable + 5-turn smoke + tier-distribution print
python scripts/ops_cascade_enable_per_bot.py \
    --bot test-spa-id --workspace dev-ws --channel web \
    --action enable

# 2c. Disable if Haiku tier produces refuse / latency regression
python scripts/ops_cascade_enable_per_bot.py \
    --bot test-spa-id --workspace dev-ws --channel web \
    --action disable
```

**Rollout cadence**: 1 bot → observe 24h cascade_routing_applied events
in `request_logs` → enable next bot. Do NOT batch-enable platform-wide;
the smoke verify is per-bot for a reason.

**Tenant-scoped UPDATE** (multi-tenant collision safety): add
`--record-tenant-id <UUID>` when the same `(workspace_id, bot_id,
channel_type)` slug tuple exists in more than one tenant.

## Phase 3 — LM Studio swap pilot (LM-1 2/6 swap-able services)

**Why**: Wave G LM-1 verdict — `llm_enrich` + `crag` swap onto in-house
`<LMSTUDIO_HOST>` gemma-4-e2b-it. Token cost drop, quality probe inside.

```bash
export LMSTUDIO_BASE_URL="${LMSTUDIO_BASE_URL}/v1"
export LMSTUDIO_API_KEY="<token>"   # optional bearer

# 3a. Pre-flight LM Studio host + current system_config rows
python scripts/ops_lmstudio_swap_pilot.py \
    --service llm_enrich --action preflight

# 3b. Swap + 5-turn smoke + 30Q HALLU trap gate (SACRED)
#     Auto-rollback on smoke error OR any HALLU breach.
python scripts/ops_lmstudio_swap_pilot.py \
    --service llm_enrich --action swap

# 3c. Repeat for crag grader after llm_enrich proves stable for ≥24h
python scripts/ops_lmstudio_swap_pilot.py --service crag --action swap

# 3d. Rollback (restores reports/ops_lmstudio_swap_<svc>_snapshot.json)
python scripts/ops_lmstudio_swap_pilot.py \
    --service llm_enrich --action rollback
```

**HALLU gate**: dataset = `tests/eval/datasets/30Q_golden_medispa.json`.
Breach = trap question answered with fabricated content (does not match
any `expect_refuse_substrings`). One breach → automatic rollback +
exit code 1.

**System-wide blast radius**: swap touches `system_config` rows that
the **whole platform** reads. Do NOT run while another bot owner is
mid-ingest — `llm_enrich` flip changes the chunk enrichment provider
for every subsequent ingest job. Coordinate with ops calendar.

## Failure mode quick-reference

| Symptom | Trigger | Recovery |
|---|---|---|
| Phase 1 smoke 401 post-restart | Worker started before .env reload | `--action rollback` then `systemctl restart document-worker` |
| Phase 2 smoke errors > 0 | Cache busts failed | Script auto-flips flag back; re-run preflight |
| Phase 3 HALLU breach > 0 | gemma quality regression | Auto-rollback; restore snapshot; do NOT retry without QA |
| Snapshot file missing | First-time use of `--action rollback` | Restore manually from `system_config` audit history |

## Idempotency contract

All three scripts are **safe to rerun**:

- Phase 1 `rotate` is a no-op if `.env` already matches `ANTHROPIC_API_KEY_NEW`.
- Phase 2 `enable` is a no-op (`UPDATE` sets same value).
- Phase 3 `swap` rewrites the same `(provider, model)` tuple.

## Quality gate

Per `CLAUDE.md` tier policy: all three scripts are `[T2-CostPerf]`. They
do **not** mutate `src/ragbot/` business logic — only `system_config`,
`bots.plan_limits`, and `.env`. Behaviour changes are config-driven and
config-reversible.
