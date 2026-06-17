# Load test runbook — anti-abuse bypass

Tier: T1 (smartness ground truth depends on synthetic probes hitting the
real pipeline; the pipeline must not flag the loadtest harness as a
suspicious source IP).

## What the bypass does

A localhost-originated request that presents a matching token in the
`X-Ragbot-Loadtest-Bypass` header skips:

- the per-IP rate-limit cap (`IpRateLimitMiddleware`),
- the anti-abuse 4xx-ratio counter and the `mark_suspicious` side-effect.

It **does NOT** skip:

- authentication (bearer token still validated end-to-end),
- RBAC level checks,
- the user-agent denylist (loadtest harness must send a browser-shaped
  UA — see `scripts/loadtest_3persona_consume.py`),
- honeypot routes (`/wp-admin`, `/.env`, …).

## Setup (one-shot per loadtest session)

```bash
# 1. Generate a single-use token. Long, random, never committed.
export RAGBOT_LOADTEST_BYPASS_TOKEN=$(openssl rand -hex 16)

# 2. Restart (or hot-reload) the API so the env var is in process memory.
#    The bypass helper reads os.environ at request time, so a `kill -HUP`
#    is enough on uvicorn-with-reload.

# 3. Run the loadtest with the matching header.
.venv/bin/python scripts/loadtest_3persona_consume.py \
    --questions reports/loadtest_3persona_questions.json \
    --output reports/LOADTEST_3PERSONA_30Q_<label>_<ts>.json
```

The harness reads `RAGBOT_LOADTEST_BYPASS_TOKEN` from the environment and
sends the matching header on every request. No source change required.

## Production policy

- Production deployments **never** set `RAGBOT_LOADTEST_BYPASS_TOKEN`.
  The helper fails closed on empty / unset env — no bypass possible.
- The bypass is **localhost-only**. Loopback peer (`127.0.0.1` / `::1`)
  is the third hard gate; a public IP presenting a valid token still
  cannot exercise the bypass.
- Token compare uses `secrets.compare_digest` (constant-time) so the
  token cannot be exfiltrated by timing oracle.

## Tear-down

```bash
unset RAGBOT_LOADTEST_BYPASS_TOKEN
# Restart / reload the API so the worker drops the value from memory.
```

The token has no persistent state — rotating it is just `unset` +
`export $(openssl rand -hex 16)` + reload.

## Observability

A successful bypass emits a single structlog event:

```
loadtest_bypass_used  path=/api/ragbot/test/chat  peer=127.0.0.1
```

The token, header value, and any user content never appear in the log.
Failed bypass attempts (env empty / header mismatch / non-loopback peer)
emit no log line — the failure mode must not advertise whether the
feature is configured.

## Why three gates instead of one

Defence-in-depth. Each gate fails closed independently:

1. **Env-token-gated** — production has no token, period.
2. **Constant-time header compare** — even with the env set, a wrong
   header value is rejected without leaking timing data.
3. **Loopback peer** — even if a token leaks, a remote attacker cannot
   exercise it; the harness must run on the same host as the API.

Removing any one gate widens the attack surface — keep all three.
