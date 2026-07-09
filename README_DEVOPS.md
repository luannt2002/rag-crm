# Ragbot — DEVOPS playbook (CI / CD / deploy)

> **Your mode: GATE owner.** You own the pipeline that turns code + seed into a running service:
> CI, the config-completeness gate, the Docker build, env/secret wiring, deploy, and the RLS
> enforcement flip. Your gate is what lets the [backend](README_DEV.md) fail-loud safely — it
> proves the [DATABASE team's](README_DATABASE.md) seed covers every key the backend reads
> **before** any image ships. Architecture overview: [README.md](README.md).

---

## 0. The ownership split in one line

**Backend owns the contract. Database owns the values. You own the gate that proves the values
cover the contract before build — so nothing half-configured ever reaches a user.**

---

## 1. Config-completeness gate {#config-completeness-gate}

This is the keystone of the whole ownership model. **Before a Docker image is built, run an
init-test against a freshly-seeded database that asserts every config key the backend reads is
present.** If any key is read but unseeded → the test fails → the build stops → it never reaches prod.

Because this gate exists, the backend is allowed to **fail loud on a missing key at runtime**
instead of silently defaulting — the gate guarantees the key can't actually be missing in a shipped image.

**What the gate checks** (the contract surface):
- every `_pcfg(state, "<key>")` the pipeline reads has a value in the seeded `system_config`;
- every `resolve_bot_limit(cfg, "<key>")` resolves against `plan_limits` / schema;
- pipeline-config parity between the HTTP route and the worker
  (`test_pipeline_cfg_keys_parity.py`) holds.

**Where it runs in the pipeline:**

```
git push
  └─► CI: alembic upgrade head  (fresh DB)
        └─► init-test: config-completeness + parity   ◀── THE GATE
              ├─ fail → stop, report missing keys to DATABASE team, no build
              └─ pass → docker build → push image → deploy → smoke test
```

Wire the gate as a required CI step (not advisory). A red gate is a seed bug the
[DATABASE team](README_DATABASE.md) fixes by adding the value — not something the backend patches
with an inline default.

---

## 2. Environment variables (from secrets, never tracked files)

**Never commit real hosts / credentials** — no brand hostname, no DB password, no internal IP in
any tracked file. Use env / secret store; placeholders below.

| Var | Purpose |
|---|---|
| `DATABASE_URL` | async app DSN (today: superuser — RLS inert; see §4) |
| `DATABASE_URL_SYNC` | sync DSN for alembic |
| `DATABASE_URL_APP` | request-path role `ragbot_app` (NOBYPASSRLS) — set on RLS flip |
| `DATABASE_URL_SYSTEM` | worker role `ragbot_system` (BYPASSRLS) — set on RLS flip |
| `RAGBOT_ALLOW_SUPERUSER_RUNTIME` | `1` = allow superuser at runtime (RLS bypassed); target `0` after flip |
| `APP_EMBED_WORKERS_ENABLED` | run the 5 embedded asyncio workers in-process |
| `RAGBOT_LOADTEST_BYPASS_TOKEN` | operator-only cache/rate-limit bypass for load tests (loopback) |
| `<PROVIDER>_API_KEY` | LLM / embedder / reranker provider keys |

Example (values are placeholders — real ones come from the secret store):

```bash
DATABASE_URL_SYNC=postgresql+psycopg2://<db-user>:<db-pass>@<db-host>:5432/<db-name>
DATABASE_URL=postgresql+asyncpg://<db-user>:<db-pass>@<db-host>:5432/<db-name>
```

---

## 3. Deploy flow

```bash
set -a && source .env && set +a
alembic upgrade head          # schema + seed migrations to head
# → CI runs the config-completeness gate here (§1). Green before proceeding.
# build image → deploy → smoke:
python -m ragbot.main         # single process: API + 5 embedded workers
```

**After any config/constant change, restart the process** — a live process holds the old module
and 500s on new symbols until reloaded:

```bash
sudo systemctl restart ragbot-py
```

**Perf knob (not a code change):** a slow LLM endpoint is handled by raising the provider
`timeout_ms` in the DB seed (owned by the [DATABASE team](README_DATABASE.md), applied via
alembic) — e.g. `innocom` 30000 → 90000ms to stop mid-generation truncation. You deploy the
migration; you do not edit code for it. Trade-off: `retry_policy max_attempts` stacks on top of
the timeout, so tune worst-case latency vs completeness together.

---

## 4. RLS enforcement flip (Phase 3) {#rls-flip}

RLS is **provisioned + code-wired but inert today**: the app connects as superuser `ragbot`
(`RAGBOT_ALLOW_SUPERUSER_RUNTIME=1`), so DB-level row-security is bypassed and live isolation
rests on the mandatory `record_bot_id` app-filter (solid; RLS is defense-in-depth).

To enforce at the DB layer (gated — needs the `ragbot_app` role credential):
1. Point `DATABASE_URL_APP` → `ragbot_app` (NOBYPASSRLS request role).
2. Point `DATABASE_URL_SYSTEM` → `ragbot_system` (BYPASSRLS — the 4 cross-tenant workers).
3. Set `RAGBOT_ALLOW_SUPERUSER_RUNTIME=0`.
4. Harden `NULLIF('')` on the `app.tenant_id` policy; run the isolation probe (tenant A → its
   rows, other tenant → 0, no-ctx → 0 fail-closed).
5. Load-test gate before promoting.

Runbook: [`plans/260619-rls-enforcement/plan.md`](plans/260619-rls-enforcement/plan.md).

> **Credential note:** minting/rotating a runtime DB role password on a real DB is a
> high-severity op — it needs explicit owner authorization, not a general "we control the box".

---

## 5. What you monitor

- **Config drift** — the gate is your guard; a green gate means the seed matches the contract.
  A stale code comment or constant is a backend concern, not a runtime risk (prod reads the DB).
- **Latency** — the dominant cost is the external LLM endpoint (measured p50 ≈ 45s, p95 ≈ 110s in
  the 2026-07-08 load test); it is external/ops, separate from answer correctness. Levers: a
  faster endpoint, or reducing LLM calls per turn — both measured, never guessed.
- **Restart hygiene** — always restart after a config/constant change (§3).

---

## 6. Your definition of done

- [ ] Config-completeness gate is a **required** CI step, red = no build.
- [ ] All secrets come from the store; zero real host/credential/IP in tracked files.
- [ ] `alembic upgrade head` → gate green → build → deploy → smoke, reproducible on a fresh DB.
- [ ] RLS flip runbook followed with owner authorization when promoting DB-level isolation.

---

*Backend contract: [README_DEV.md](README_DEV.md). Config values / seed:
[README_DATABASE.md](README_DATABASE.md). Architecture: [README.md](README.md).*
