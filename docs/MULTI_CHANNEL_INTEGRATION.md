# Multi-Channel Integration Guide — Ragbot

> Owner: Platform team
> Audience: integrators, channel-onboarding ops, bot owners
> Scope: how the same bot codebase serves multiple channels (web / messenger / zalo / line / …)
> Domain-neutral: contract + identifier shape only — no tenant literal.

Cross-references:
- [`docs/master/10-J-channel-integration.md`](master/10-J-channel-integration.md) — formal HTTP contract spec.
- [`ZALO_MASTER.md`](../ZALO_MASTER.md) — Zalo backend details.
- [`docs/DR_RUNBOOK.md`](DR_RUNBOOK.md) — recovery rules per channel.
- 3-key identity rule: [`CLAUDE.md`](../CLAUDE.md) IDENTITY RULE section.

---

## 0. Mental model — channel_type is part of identity, not a flag

A "bot" on this platform is **NOT** identified by a single slug. It is the
3-key triple `(tenant_id, bot_id, channel_type)`. Two consequences:

1. The same `bot_id` ("support") on `channel_type="web"` and `channel_type="messenger"`
   are **two distinct rows** in `bots` with two different `record_bot_id` UUIDs.
   They can have different system_prompt, oos_answer_template, custom_vocabulary,
   model bindings, even different document corpora.
2. Cache scopes (Redis, semantic_cache, model_resolver) include channel_type.
   Cross-channel data leak is structurally prevented.

This means: **adding a channel is NOT branching the codebase; it is registering
a new (bot_id, channel_type) row** and pointing the channel-specific gateway
at the same Python service.

---

## 1. Currently supported channels

| Channel | Status | Gateway | Notes |
|---|---|---|---|
| `web` | **Production** | direct HTTP `/api/ragbot/chat` | tested daily; load-test stack |
| `zalo` | **Beta** (impl partial) | external Node.js (`ZALO_MASTER.md`) → 4 contract endpoints | OA token + signature handled in Node |
| `messenger` | **Planned** Q3-2026 | Facebook webhook → adapter service | see §3 |
| `line` | **Planned** Q4-2026 | LINE webhook → adapter service | see §4 |
| `telegram` | Future | bot-api long-poll → adapter | low priority |

---

## 2. Web channel — production reference

**Identity**: `channel_type = "web"`.

**Request shape** — `POST /api/ragbot/chat` (3-key REQUIRED, Pydantic validates):

```json
{
  "tenant_id": 12345,
  "bot_id": "support",
  "channel_type": "web",
  "connect_id": "user-abc",
  "message_id": 1774946011723,
  "message": "Câu hỏi từ user...",
  "trace_id": "uuid-..."
}
```

**Behavior**:

- 422 if any of `tenant_id` / `bot_id` / `channel_type` is missing or null.
- 403 if JWT/header `tenant_id` does not match body `tenant_id`.
- 200 with full response body (synchronous).
- Streaming variant: `POST /api/ragbot/chat/stream` SSE.

**Per-channel config available today** (web bot row):

- `system_prompt` — full bot persona + business rules.
- `oos_answer_template` — refusal text when no docs match.
- `custom_vocabulary` JSONB — abbreviation expansion.
- `setting_options` JSONB — pipeline toggles (rerank intent whitelist, etc.).
- `callback_url` — set if the channel needs async push-back (web typically NULL).

---

## 3. Messenger channel — integration plan (NOT yet implemented)

This section documents the **approach**, not running code. Implementation is deferred.

### 3.1 Gateway adapter

A small adapter service (Node.js or Python — TBD) exposes:

- `GET /messenger/webhook` — Facebook subscription verification (`hub.mode=subscribe`, signed challenge response).
- `POST /messenger/webhook` — Facebook delivers message events. Signature header `X-Hub-Signature-256` verified using App Secret.

The adapter:

1. Validates the Facebook signature (HMAC-SHA256).
2. Maps Facebook `sender.id` → ragbot `connect_id`.
3. Calls Python `POST /api/ragbot/chat` with `channel_type="messenger"`.
4. Receives the answer → calls `https://graph.facebook.com/v18.0/me/messages` with the page access token.

### 3.2 Identifier mapping

| Facebook field | Ragbot field |
|---|---|
| `entry[].messaging[].sender.id` (PSID) | `connect_id` |
| `entry[].id` (page ID) | resolves to `tenant_id` via `tenant_channel_bindings` table (future) |
| `entry[].messaging[].message.mid` | `message_id` (string-cast to int hash if needed) |
| Adapter-generated UUID | `trace_id` |

### 3.3 Per-channel config the bot owner sets

Same 5 columns as web (§2). A messenger row commonly differs from web by:

- Shorter `system_prompt` answers — Messenger UX favors ≤ 500 char replies.
- Different `oos_answer_template` — channel-specific tone.
- `callback_url` may be set if async response > 5s (Messenger has 20s window).

### 3.4 Required secrets (kept in `.env`, NOT in code)

```
MESSENGER_APP_SECRET=...           # for signature verification
MESSENGER_PAGE_ACCESS_TOKEN_<TID>=...  # per-tenant page token
MESSENGER_VERIFY_TOKEN=...         # webhook subscription token
```

Per-tenant tokens live in a future `channel_credentials` table (encrypted column),
NOT `.env`. `.env` only holds the **app-level** secret.

### 3.5 Open questions before impl

- [ ] Adapter language: Node (reuse Zalo gateway pattern) vs. Python (one less hop)?
- [ ] Where does PSID → connect_id mapping persist? `users` table extension or new `channel_users`?
- [ ] Rate-limit interaction with Facebook policy (200 calls/sec/page).

---

## 4. Line channel — integration plan (Q4-2026, NOT scoped)

Mirror §3 with LINE specifics:

- Webhook signature: `X-Line-Signature` (HMAC-SHA256 over body).
- Identifier: `events[].source.userId` → `connect_id`.
- Reply token model: LINE expects reply within 30s using `replyToken` (one-shot).
  If late, use push API.
- Channel access token per tenant (long-lived, refreshable).

Defer until Messenger is in production.

---

## 5. Per-channel sysprompt override

This is **not a code feature** — it's an **operational pattern** enabled by the
3-key identity:

```
bot_owner creates 2 bots row in DB:
  - (tenant_id=12345, bot_id="support", channel_type="web")     → system_prompt="Long detailed..."
  - (tenant_id=12345, bot_id="support", channel_type="messenger") → system_prompt="Short ≤500 char..."
```

When a request arrives with `channel_type="messenger"`, `BotRegistryService.lookup`
resolves to the second row → uses its system_prompt. **Application code is identical**
across channels; the only thing varying is the row chosen.

This is the "single source of truth = bot owner" principle (see CLAUDE.md
Application MINDSET) applied per-channel.

### 5.1 What if the bot owner did NOT create a messenger row?

`lookup()` returns `None` → HTTP 404 with `{"error": "bot_not_registered"}`.
The platform does **not** silently fall back to a web row — that would violate
3-key identity and create a cross-channel pseudo-leak.

Onboarding flow: tenant explicitly creates a row per channel they want to enable.

---

## 6. Channel isolation guarantees

Pinned by tests in `tests/unit/test_channel_isolation.py`:

| Guarantee | How it's enforced |
|---|---|
| Two bots same `bot_id`, different `channel_type` → different `record_bot_id` UUIDs | DB unique `(tenant_id, bot_id, channel_type)`, all NOT NULL |
| Semantic cache scoped per `record_bot_id` | `semantic_cache.record_bot_id` FK, query filter |
| Bot registry Redis key is per-channel | `ragbot:bot:{tenant_id}:{bot_id}:{channel_type}` |
| Document corpus scoped per `record_bot_id` | `documents.record_bot_id` FK |
| Model bindings scoped per `record_bot_id` | `bot_model_bindings.record_bot_id` FK |
| No code path bypasses `record_bot_id` after resolve | grep audit (see CLAUDE.md anti-pattern list) |

---

## 7. Adding a new channel — checklist for engineering

Use this when adding (e.g.) `whatsapp`:

1. **No code change** in `src/ragbot/orchestration/` or `src/ragbot/application/services/`.
   `channel_type` is opaque string downstream of resolve.
2. **Build adapter** at `src/ragbot/interfaces/adapters/whatsapp_adapter.py` (or external service).
   Adapter only does: signature verify → map IDs → call `/api/ragbot/chat` with `channel_type="whatsapp"`.
3. **Add secrets** to `.env.example` (placeholder only) and document in this file §X.
4. **Add unit test** mirroring `tests/unit/test_channel_isolation.py` ensuring the
   new channel_type does not leak into other channels' caches.
5. **Update master spec** at [`docs/master/10-J-channel-integration.md`](master/10-J-channel-integration.md).
6. **Smoke test**: create one row `(tenant=test_tenant, bot_id=demo, channel_type=whatsapp)`,
   POST a question, confirm 200 + correct system_prompt invoked.

That's it. No orchestrator change. The Strategy + DI architecture (CLAUDE.md
Strategy mindset) means channels compose horizontally.

---

## 8. Common failure modes & fixes

| Symptom | Cause | Fix |
|---|---|---|
| HTTP 422 "channel_type required" | Client forgot field | Pydantic schema works as designed |
| HTTP 404 "bot_not_registered" | No row for given 3-key | Bot owner creates row in `bots` |
| Cross-channel cache hit (rare) | Pre-0048 row had `channel_type=NULL` | Run migration; column NOT NULL |
| Wrong system_prompt used | Adapter passed wrong `channel_type` literal | Verify adapter sends exact channel_type the row uses |
| Redis stampede on cold start | All channels hit DB simultaneously after cache flush | `bot_registry` lock-with-redis pattern handles this |

---

## 9. Roadmap

| Phase | Channel | Target |
|---|---|---|
| Done | web | production |
| Now | zalo | beta → GA |
| Q3-2026 | messenger | adapter MVP |
| Q4-2026 | line | adapter MVP |
| 2027 | whatsapp / telegram | based on demand |

Channel adapters are independent deliverables. Platform code is **complete** for
channel polymorphism today; remaining work is per-channel integration only.
