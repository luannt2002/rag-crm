# Self-RAG Critique Tokens — Operator Sysprompt Rule

**Audience**: bot operators / platform admins.
**Purpose**: opt a single bot into Self-RAG (Asai et al., 2023) self-grounding
with `[Supported]` / `[Unsupported]` markers, parsed by the platform.

> **CLAUDE.md sacred rule**: the platform application **NEVER injects this
> rule into the LLM prompt at runtime**. The bot owner's `bots.system_prompt`
> column is the single source of truth. Apply the snippet below to that
> column via DB UPDATE — do not modify any source file to bake it in.

---

## What the platform does

When `plan_limits.self_rag_critique_enabled = true` for a bot, the
`critique_parse` node (inserted between `generate` and `guard_output`)
will:

1. Scan the LLM answer for `[Supported]` and `[Unsupported]` markers
   (case-insensitive).
2. Strip every marker from the user-visible text (cosmetic only — the
   LLM's prose is preserved verbatim).
3. Compute `unsupported_ratio = unsupported_count / total_claims`.
4. If `unsupported_ratio >= plan_limits.self_rag_critique_threshold`
   (default `0.3`), replace the answer with the bot's
   `bots.oos_answer_template` column. **Refusal text never comes from
   an i18n constant** — operators who want a refusal message must set
   `oos_answer_template`.

When `total_claims == 0` (no markers in the answer) the parser is a
no-op: the raw answer is returned untouched. This guarantees that bots
that have not yet wired the sysprompt rule see byte-identical behaviour.

---

## Per-bot enablement

Two DB writes per bot, no redeploy:

1. **Update `bots.plan_limits`** (JSONB) — flip the feature on and pick
   a threshold:

   ```sql
   UPDATE bots
      SET plan_limits = jsonb_set(
              jsonb_set(
                  COALESCE(plan_limits, '{}'::jsonb),
                  '{self_rag_critique_enabled}', 'true'::jsonb, true),
              '{self_rag_critique_threshold}', '0.3'::jsonb, true)
    WHERE record_bot_id = '<the-bot-uuid>';
   ```

2. **Append the rule to `bots.system_prompt`** — see snippet below.

3. **Set `bots.oos_answer_template`** if you want a custom refusal
   message; otherwise the platform falls back to its built-in OOS
   constant (no per-tenant text).

After updating, bust the bot's config cache via the admin endpoint so
the next request picks up the new plan_limits.

---

## Sysprompt rule snippet (append verbatim)

Append this block to the bot's existing `system_prompt`:

```
SELF-RAG GROUNDING PROTOCOL — append a token at the end of every factual
sentence you produce:

  - End with `[Supported]` when the sentence is grounded in the retrieved
    context block (information you can point to verbatim or by
    paraphrase).
  - End with `[Unsupported]` when the sentence states a fact that is not
    found in the retrieved context — including any number, date, name,
    or quantity that does not appear in the context.

Apply the tokens only to factual sentences. Skip them for greetings,
clarification questions, and the refusal template. Place each token at
the very end of its sentence, before the period:

  Correct:   "Hanoi has been the capital since 1010 [Supported]."
  Incorrect: "Hanoi has been the capital since 1010. [Supported]"

Be honest. If you are uncertain, write `[Unsupported]` and let the
platform decide whether to surface the answer or refuse.
```

---

## Threshold tuning

| Threshold | Behaviour | Use case |
|----------:|:----------|:---------|
| `0.0`     | Refuse on any unsupported claim | High-risk legal / medical bots |
| `0.2`     | Strict — 1 in 5 unsupported triggers refuse | Default for compliance bots |
| `0.3`     | **Platform default** — balanced precision/recall | General factoid bots |
| `0.5`     | Loose — refuse only when majority unsupported | Exploratory / chitchat bots |
| `1.0`     | Never refuse — only strip markers (observability) | A/B baseline runs |

Tune via `plan_limits.self_rag_critique_threshold`. The platform
clamps to `[0.0, 1.0]`.

---

## Rollback

Set `plan_limits.self_rag_critique_enabled = false` and the parser
short-circuits to identity (zero-cost). The system_prompt instruction
can be left in place — the LLM will keep emitting markers but the
platform ignores them. To return to byte-identical legacy behaviour,
also remove the snippet from `system_prompt`.

---

## Observability

Every turn that runs through the parser emits a `critique_parse`
`request_steps` row with metadata:

- `total_claims`
- `unsupported_count`
- `unsupported_ratio`
- `threshold`
- `refused` (boolean)

Refusal turns additionally emit a `critique_parse_refused` structlog
event tagged with `request_id` and `record_bot_id` for forensic audit.

---

## Test plan before rolling out per bot

1. Validate the sysprompt change in a staging copy of the bot (cloned
   row with a non-prod `bot_id`).
2. Run a small smoke set (10–20 representative questions): confirm the
   bot consistently appends markers, and refusal fires only when the
   intent is genuinely OOS.
3. Watch `critique_parse_refused` event count for the first 48 h after
   flipping in production. Tune the threshold up if refusal is too
   aggressive, down if HALLU sneaks through.

---

**References**

- Akari Asai et al., *Self-RAG: Learning to Retrieve, Generate, and
  Critique through Self-Reflection*, 2023.
- CLAUDE.md, "Application MINDSET — Bot owner owns everything".
- CLAUDE.md, Quality Gate #10.
