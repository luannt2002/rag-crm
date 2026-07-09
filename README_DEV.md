# Ragbot — DEV playbook (BE developer)

> **Your mode: CONTRACT owner.** You own the code, the pipeline logic, and the config
> **contract** — the *key names* + *types* + *validation* the backend reads. You do **NOT**
> own config *values* (that is the [DATABASE team](README_DATABASE.md)) and you do **NOT**
> own the deploy gate (that is [DevOps](README_DEVOPS.md)). Architecture overview:
> [README.md](README.md). Sacred rules you must obey: [CLAUDE.md](CLAUDE.md).

---

## 0. The one rule that shapes everything you write

**Config VALUES do not live in code. The backend reads them from the database and
fails loud if a key it needs is missing.**

The runtime resolve chain (high → low priority) is:

```
bots.threshold_overrides → bots.<column> → bots.plan_limits → system_config → (schema default)
```

Everything the bot actually runs on in prod comes from **`system_config`** (platform
defaults, Redis-cached) and **`plan_limits`** (per-bot overrides) — both in the DB, both
owned by the DATABASE team's seed. Your job is to **declare the contract**, not to hardcode
the value:

- **Declare** which keys you read + their type (`_pcfg(state, "key")`, `resolve_bot_limit(cfg, "key")`).
- **Read** from the resolved config, never a literal.
- **Fail loud** if a required key is absent — do not paper over it with an inline default.
  A missing key is a seed bug (DATABASE team) that the CI gate ([DevOps](README_DEVOPS.md))
  must have caught *before* build. It is not the backend's job to guess a value at runtime.

This is why the platform can be strict: the [init-test gate](README_DEVOPS.md#config-completeness-gate)
proves every key you read is seeded before any Docker image ships. Trust the gate; read the DB; fail loud.

> **Zero-hardcode (sacred):** no magic number / model name / brand / threshold inline in
> `src/`. Pure-technical constants (timeout, retry, batch) live in `shared/constants/`; all
> behavior/threshold values live in the DB seed. `0`/`1`/`100`/indices are the only inline literals allowed.

---

## 1. Local dev loop

```bash
# 1. env (never commit real secrets — see DevOps for the full var list)
set -a && source .env && set +a         # DATABASE_URL, provider keys, etc.

# 2. schema up to head
alembic upgrade head

# 3. run the single process (API + 5 embedded asyncio workers)
python -m ragbot.main

# 4. tests
pytest tests/unit -q                     # unit (fast)
pytest tests/unit/test_pipeline_cfg_keys_parity.py -q   # config-contract parity
```

> **After editing anything in `shared/constants/` or a config contract, restart the running
> process** (`sudo systemctl restart ragbot-py` on the box) — a live process holds the old
> module and will 500 on the new symbol until reloaded. This bit us in the 2026-07-08 session.

---

## 2. What you own — the codebase map

| Layer | Path | You own |
|---|---|---|
| HTTP API (the product) | `interfaces/http/routes/` | request/response schemas, `X-Schema-Version` header negotiation |
| Query pipeline | `orchestration/query_graph.py` + `orchestration/nodes/` | the ~21 LangGraph nodes |
| Ingest pipeline | `application/services/document_service/` | U0–U7 stages |
| Ports / adapters | `application/ports/`, `infrastructure/<thing>/` | Strategy + Registry + Null-Object per swap-able thing |
| Config contract | `interfaces/**/pipeline_config.py`, `shared/bot_limits.py` | key names + types the BE reads |
| Constants (technical only) | `shared/constants/` | timeout/retry/batch — NOT behavior values |
| Anti-HALLU guards | `orchestration/nodes/guard_output.py`, `shared/{claim_fidelity,brand_scope}.py` | deterministic gates |

Architecture detail (pipeline node-by-node, identity, RLS) stays in [README.md](README.md).

---

## 3. Sacred rules — the pre-commit checklist (from [CLAUDE.md](CLAUDE.md))

Before every commit, self-audit:

1. **HALLU = 0** — the bot never fabricates a number/fact. Deterministic guards
   (numeric-fidelity, brand-scope, claim-fidelity, empty-answer) run `observe → block` per-bot.
2. **App never injects text into the LLM prompt, never overrides the LLM answer.** The bot
   owner's `system_prompt` is the single source of truth. No regex replace on the answer, no
   i18n fallback text, no platform rule prepended. (One governed exception: `SysPromptAssembler`
   *appends* alembic-tracked platform rules — see CLAUDE.md.)
3. **Zero-hardcode** — §0 above.
4. **Domain-neutral** — no brand / industry / customer literal in `src/`. Domain data →
   `custom_vocabulary` / per-file manifest / DB config. The engine knows *structure* (value-shape),
   not vocabulary.
5. **No version-ref** — no `_v1`/`_v2`/`_legacy`/`_new`/`_old` in names, columns, URLs, schema
   classes. Names reflect PURPOSE. API versioning is header-based (`X-Schema-Version`), never URL.
6. **4-key identity** — `(record_tenant_id, workspace_id, bot_id, channel_type)` at the resolve
   boundary; `record_bot_id` UUID for internal queries. Never fewer.
7. **Narrow exceptions** — no bare `except Exception:` outside the 3 allowed cases (top-level
   entrypoint / `finally` cleanup / background wrapper); use the narrow classes in `shared/errors.py`.
8. **Strategy + DI** — no hardcoded provider; every swap-able thing via Port + Registry + Null-Object + DI.
9. **Tenant isolation** — every DB query scoped by `record_bot_id` / `record_tenant_id`.
10. **RBAC** — `require_min_level(...)` numeric levels from `shared/rbac.py`, no role string literals.

Grep guards (all expect **0 hits** — run before commit):

```bash
grep -rnE "(_v[0-9]|_legacy|_new|_old)" src/ragbot/ | grep -v __pycache__ | grep -v alembic/
grep -rnE 'if.*provider.*==|provider == "(cohere|openai|jina|anthropic)"' \
  src/ragbot/orchestration/ src/ragbot/application/services/
grep -rnE "/v[0-9]+/|class\s+\w+V[0-9]+\b" src/ragbot/ | grep -v __pycache__
```

---

## 4. Adding a new config key (the contract handshake)

When your code needs a new tunable:

1. **Type + read it** in code via `_pcfg(state, "my_new_key")` / `resolve_bot_limit(cfg, "my_new_key")`.
2. **Register it** in the pipeline-config contract (`interfaces/http/routes/test_chat/_pipeline_config.py`
   **and** `interfaces/workers/chat_worker/pipeline_config.py` — keep parity, there is a test for it).
3. **Hand the key + type + intended default to the [DATABASE team](README_DATABASE.md)** so they add
   it to the seed. You define the *contract*; they define the *value*.
4. The [CI init-test gate](README_DEVOPS.md#config-completeness-gate) will fail the build if the key
   is read but not seeded — that is the safety net that lets you fail-loud instead of defaulting.

**Do not** ship a `key or SOME_INLINE_DEFAULT` fallback to "be safe". That silent default is exactly
the drift the ownership split removes: prod would run on a value nobody chose, unreproducible on a
fresh DB. Fail loud; let the gate catch the missing seed.

---

## 5. Anti-hallucination guards (deterministic, observe→block)

The bot is refusal-safe by construction. These live in `orchestration/nodes/guard_output.py`
and fire per-bot (action `observe` measures false-positives, `block` enforces):

| Guard | File | Catches |
|---|---|---|
| numeric-fidelity | `shared/document_stats.py` path | a number in the answer not present in served context/DB |
| brand-scope | `shared/brand_scope.py` | a "we don't carry brand X" reply when the index actually stocks X |
| claim-fidelity | `shared/claim_fidelity.py` | non-numeric scope-over-extension vs served text |
| empty-answer | `guard_output.py` | blank answer → bot's `oos_answer_template` |

Ladder discipline: ship a new guard in **observe** first, measure false-positive rate on a real
load-test, only then flip to **block** (per-bot, via DB seed — you write the code, DATABASE team
flips the value). Never enable two fixes at once — you can't attribute the delta.

---

## 6. Model tier for Claude Code agents (dev tooling, not the app)

When *you* drive a Claude Code agent on this repo: **Opus** in the main session for every
Edit/Write/commit/deepdive; **Sonnet** only in read-only research subagents; **Haiku banned**.
The *app's* LLM is a separate concern — it is resolved per bot via `bot_model_bindings`
(a value the DATABASE team seeds), never hardcoded here.

---

*This file is a role playbook. Product architecture, pipeline internals, and the RLS design
live in [README.md](README.md). Config values live in the DB seed
([README_DATABASE.md](README_DATABASE.md)). The build gate lives in
[README_DEVOPS.md](README_DEVOPS.md).*
