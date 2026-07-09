# Ragbot — DATABASE playbook (data / database team)

> **Your mode: VALUE owner.** You own every config **value** the platform runs on — the
> `system_config` seed, the alembic seed migrations, and all bot-config content. The
> [backend](README_DEV.md) only declares the *contract* (which keys exist + their type); you
> supply the *values*. If a key the backend reads is not in your seed, the
> [CI gate](README_DEVOPS.md#config-completeness-gate) fails the build **before** it reaches a
> user — that is by design, so a missing value is caught by you, not by a customer.
> Architecture overview: [README.md](README.md).

---

## 0. The ownership split in one line

**Backend owns the contract. You own the values. DevOps owns the gate that proves your seed
is complete before ship.**

The runtime resolve chain (high → low) is:

```
bots.threshold_overrides → bots.<column> → bots.plan_limits → system_config → (schema default)
```

Prod reads **`system_config`** (platform-wide values you seed) and **`plan_limits`** (per-bot
overrides). These are the source of truth at runtime — not any code constant. When the backend
adds a key to the contract, it is **your job** to give that key a value in the seed. A key that
is read but unseeded is a release blocker, caught by the init-test gate.

---

## 1. What you own

| Thing | Where | Rule |
|---|---|---|
| Platform config values | `system_config` table, seeded by `init_system_config.py` + alembic UPSERTs | complete = every contract key has a value |
| Per-bot overrides | `bots.plan_limits`, `bots.<column>`, `bots.threshold_overrides` | per-tenant behavior, no per-bot code |
| Bot content | `bots.system_prompt`, `bots.oos_answer_template`, `language_packs.content` | **alembic-tracked or admin-UI-audited only** |
| Model catalog | `ai_models`, `ai_providers`, `bot_model_bindings` | provider/model/timeout values |
| Guardrail config | `guardrail_rules` | per-rule action + response_message |
| Schema + RLS | alembic migrations, RLS policies/roles | DDL only via alembic |

---

## 2. The sacred rule you must never break — no psql hot-fix

**Every change to DB *content* goes through (a) an alembic migration tracked in git, OR
(b) the admin UI with an `audit_log` trail. Never a manual `psql UPDATE`.**

Protected tables (manual `UPDATE` = forbidden):

```
bots.system_prompt · bots.oos_answer_template · bots.plan_limits ·
language_packs.content · system_config.value ·
ai_models.* · ai_providers.* · bot_model_bindings.*
```

**Why:** a manual `psql UPDATE` is out-of-band drift — it does not reproduce on another DB,
cannot be rolled back, and silently breaks when a DB is cloned. A real bug (a mis-aggregated
price) was traced to exactly this: a sysprompt edited by hand with no alembic trail. Backup-file
+ one-shot psql script is **absolutely forbidden**.

Every seed migration must be **idempotent** (guard on current value) and **reversible**
(`downgrade` restores the prior value). Example shape:

```python
def upgrade() -> None:
    op.execute("""
        UPDATE ai_providers SET timeout_ms = 90000
        WHERE name = 'innocom' AND timeout_ms = 30000
    """)
def downgrade() -> None:
    op.execute("""
        UPDATE ai_providers SET timeout_ms = 30000
        WHERE name = 'innocom' AND timeout_ms = 90000
    """)
```

---

## 3. How to add / change a config value

**New key** (backend added it to the contract and handed you the key + type + intended default):
1. Add it to the seed (`init_system_config.py` for a platform default, or a new alembic UPSERT).
2. Verify locally: fresh DB → `alembic upgrade head` → run the init-test
   ([DevOps](README_DEVOPS.md#config-completeness-gate)) → it must pass (proves the key is present).

**Change an existing default:** write an alembic UPSERT (idempotent + reversible). Keep the four
declaration sites in sync so a fresh clone matches prod:
`system_config` seed **·** `bot_limits.py` schema default **·** `init_system_config.py` **·** the alembic UPSERT.
Never edit the value by hand in the running DB.

**Per-bot behavior** (one bot differs from the platform default): set `plan_limits` /
`threshold_overrides` on that bot — again via alembic or the audited admin UI. There is **no
per-bot branch in code**; behavior differences are always data.

---

## 4. Config layering — which value wins

For any key, the first layer that has a value wins:

| Priority | Layer | Owner | Scope |
|---|---|---|---|
| 1 (highest) | `bots.threshold_overrides` | you (per-bot) | one bot, forensic tuning |
| 2 | `bots.<column>` | you (per-bot) | one bot, typed column |
| 3 | `bots.plan_limits` | you (per-bot) | one bot, JSON limits |
| 4 | `system_config` | **you (platform)** | all bots — **the main seed you own** |
| 5 (lowest) | schema default | backend contract | last-resort only; being removed |

Layer 4 (`system_config`) is where the platform's real behavior is defined. Keep it complete and
correct; everything above it is per-bot exception, everything below it is meant to disappear once
the fail-loud + gate model is fully in place.

---

## 5. Model catalog + guardrail values you seed

- **Model binding**: each bot's LLM/embedder/reranker is resolved from `bot_model_bindings`
  → `ai_models` → `ai_providers`. Provider `timeout_ms` is a value you own (e.g. a slow
  endpoint may need 90000ms). The backend reads it; it never hardcodes it.
- **Guardrail rules** (`guardrail_rules`): each rule's action (`observe`/`block`) and
  `response_message` are values you seed. The backend ships a guard in `observe`; you flip it to
  `block` per-bot once false-positive rate is measured. Refusal text comes from
  `bots.oos_answer_template` or a rule's `response_message` — **never** a hardcoded i18n string.

---

## 6. Your definition of done

- [ ] Every key the backend contract reads has a value in the seed (init-test gate green).
- [ ] Every content/value change is an alembic migration (idempotent + reversible) or admin-UI-audited.
- [ ] Zero manual `psql UPDATE` on the protected tables.
- [ ] Fresh DB → `alembic upgrade head` → gate passes → the clone behaves identically to prod.

---

*Backend contract: [README_DEV.md](README_DEV.md). Build/deploy gate:
[README_DEVOPS.md](README_DEVOPS.md). Architecture: [README.md](README.md).*
