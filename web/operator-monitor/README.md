# Operator monitor

Minimal HTML / vanilla ES module page for watching ragbot live state
(corpus, recent load tests, documents). Read-only viewer — calls public
analytics APIs only. Domain-neutral: no brand / tenant baked in.

## Run locally

```bash
python3 -m http.server -d web/operator-monitor/ 8080
# then open http://localhost:8080/
```

## Configure API base

Default points at `http://localhost:3004`. Override via either:

```bash
# Browser URL query
http://localhost:8080/?api_base=https://your-host.example.com

# Or set window.RAGBOT_API_BASE before script load (edit index.html)
```

## Set bot 3-key identity

Three keys are required for any tenant-scoped query
(`tenant_id`, `bot_id`, `channel_type`):

```
http://localhost:8080/?tenant_id=<TENANT_ID>&bot_id=<BOT_ID>&channel_type=web
```

Or set via the form on the page (saved to localStorage keys
`ragbot_tenant_id`, `ragbot_bot_id`, `ragbot_channel_type`).

## Trend chart

The trend panel renders inline SVG bars. Pass a comma-separated list of
JSON URLs whose payload matches `mega_round*.json` (must include
`summary.rates_pct.PASS`):

```
?trends=https://host/round7.json,https://host/round8.json,https://host/round-fresh.json
```

Without `?trends=`, the chart panel shows a hint and stays empty.

## Auto-refresh

All panels refresh every 30 s. The log-tail panel is manual-only.

## Endpoints used

- `GET /api/ragbot/test/tokens/self` — DEV self-token (loopback only)
- `GET /api/ragbot/admin/analytics/bots/pass-rate`
- `GET /api/ragbot/admin/analytics/bots/cost`
- `GET /api/ragbot/admin/analytics/bots/latency`
- `GET /api/ragbot/sync/documents?tenant_id=&bot_id=&channel_type=`
- `GET /health`

## Constraints

- No frameworks (no React / Vue / jQuery)
- No external CDN dependency
- No hard-coded credentials, tenant, or brand
- 3-key strict on every per-bot query
