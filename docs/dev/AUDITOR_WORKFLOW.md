# Auditor-Chief Workflow — `audit_agent_diff` + async-mindset

Reproducible pre-merge / pre-commit gate for coder + agent campaign
branches. Wraps existing CLAUDE.md grep guards (zero-hardcode,
version-ref, brand literal, model literal, resolver fallback,
domain-neutral, tenant secrets, async mindset) into two scripts that
the Auditor-Chief role (or its CI surrogate) runs on every coder
hand-off.

The CI surrogate is `.github/workflows/audit-agent-diff.yml`, which
runs on every PR to `main`, `coder-*`, `agent-*`, or `mom-*`.

---

## Quickstart

```bash
# Audit a coder branch before merging to main.
bash scripts/audit_agent_diff.sh agent-260518-A2-cont-ci-mindset main

# Audit a feature branch against a sibling base (e.g. when a campaign
# uses a non-main base like `coder-260518-W1-recovery-worker`).
bash scripts/audit_agent_diff.sh agent-260518-A2-cont-ci-mindset \
    coder-260518-W1-recovery-worker

# Async-mindset heuristic scan (warn only by default).
bash scripts/audit_async_mindset.sh

# Async-mindset in CI/pre-commit gate mode (fail on findings).
bash scripts/audit_async_mindset.sh --strict
```

---

## What each guard catches

### `scripts/audit_agent_diff.sh` — 4 guards in one wrapper

The wrapper worktree-checkouts BOTH feature and base into temp dirs,
runs each helper against each ref, then computes a delta.
**Default mode is `--regression-only`**: a pre-existing violation is
tolerated; only a NEW violation introduced by the feature branch
fails the gate.

| Guard | Helper | Catches |
|-------|--------|---------|
| 1 | `scripts/anti_hardcode_check.sh` | inline magic numbers · `mock_data=` / `fake_response=` fixtures in `src/` · `TODO/FIXME/XXX/HACK` crumbs · `Sprint S\d+`, `V\d+.\d+.\d+`, `Round V[A-Z]`, `post-V\d+` version-refs · `gpt-4`, `claude-3`, `gemini-1.5`, `voyage-3`, etc. model-name literals · delegates to `scripts/grep_domain_literals.sh` for brand vocabulary |
| 2 | `scripts/audit_resolver_fallback.sh` | every per-bot resolver under `application/services/*resolver*.py` MUST contain `_lookup_platform_default`, `system_config`, `resolve_fallback_chain`, or an explicit `# fail-loud` decision when it queries `bot_model_bindings` |
| 3 | `scripts/audit_domain_neutral.sh` | VN diacritics in logic code (non-comment, non-docstring) · `re.compile()` patterns with VN diacritics |
| 4 | inline tenant-literal scan | `postgresql://user:pass@host/db` with embedded creds · brand hostnames (`*.vn`, `*.com`) outside localhost · IPv4 literals outside loopback |

`--strict` flag flips the gate to fail on ANY hit (ignoring base).
Useful once the baseline is clean.

### `scripts/audit_async_mindset.sh` — CLAUDE.md Async rule heuristics

Two pattern-based grep heuristics (intentional false-positive
tolerance, see "Tuning" below):

- **H1** — Two adjacent `await self._redis.*` (or `self._db.*`,
  `self._cache.*`) calls within 3 lines of each other. Likely
  `asyncio.gather()` candidate per Async Rule 1.

- **H2** — An `asyncio.gather(...)` call whose first 6 lines contain a
  side-effect verb token (`set`, `setex`, `sadd`, `hset`, `publish`,
  `xadd`, `write`, `invalidate`, `cache`, `emit`, `audit`, `log`,
  `delete`, `incr`, `expire`, `push`, `notify`, `fire`) but does NOT
  pass `return_exceptions=`. Violates Async Rule 5.

Default exit code is 0 even when findings exist (warn-only).
`--strict` converts findings into exit 1.

---

## Common violations + how to fix

### Inline magic number

```python
# WRONG
timeout = 30
max_retries = 5
```

```python
# RIGHT
from ragbot.shared.constants import DEFAULT_LLM_TIMEOUT_S, DEFAULT_HTTP_RETRY_MAX
timeout = DEFAULT_LLM_TIMEOUT_S
max_retries = DEFAULT_HTTP_RETRY_MAX
```

If the value is per-tenant tunable, also expose a `system_config` key
and read via `SystemConfigService.get_int(...)`.

### Version-ref in symbol name

```python
# WRONG
DEFAULT_EMBEDDING_COLUMN_V3 = "embedding_v3"
def migrate_to_v4(): ...
```

```python
# RIGHT
DEFAULT_EMBEDDING_COLUMN = "embedding"  # dim lifted from spec at runtime
def migrate_legacy_dim_to_target_dim(): ...  # purpose, not version
```

### Hardcoded model literal

```python
# WRONG
client.chat.completions.create(model="gpt-4-turbo", ...)
```

```python
# RIGHT — resolve via ai_models / bot_model_bindings
model_row = await self._model_resolver.resolve(
    record_bot_id=record_bot_id,
    purpose=ModelPurpose.LLM,
)
client.chat.completions.create(model=model_row.provider_model_id, ...)
```

### Resolver without `system_config` fallback

```python
# WRONG — fails when bot has no per-bot binding
class RerankerResolver:
    async def resolve(self, record_bot_id):
        row = await self._db.execute(
            "SELECT * FROM bot_model_bindings WHERE record_bot_id = :b",
            {"b": record_bot_id},
        )
        return row.first() or NullReranker()
```

```python
# RIGHT — fall back to platform default in system_config
class RerankerResolver:
    async def resolve(self, record_bot_id):
        row = await self._lookup_bot_binding(record_bot_id)
        if row is None:
            row = await self._lookup_platform_default()  # system_config JOIN ai_models
        if row is None:
            return NullReranker()  # explicit choice, not silent fallback
        return build_reranker(row)
```

### VN diacritics in logic code

```python
# WRONG
if intent == "định nghĩa": ...
SPECIAL_TOKENS = {"điều", "khoản"}
```

```python
# RIGHT — move to language_packs DB row, look up at runtime
intent_translations = await self._language_pack.get_intent_map(language)
if intent == intent_translations["definition"]: ...
```

### Adjacent sequential redis awaits

```python
# WRONG — H1 violation
async def warm_cache(self, key):
    bot_data = await self._redis.get(f"bot:{key}")
    workspace_data = await self._redis.get(f"ws:{key}")
    return bot_data, workspace_data
```

```python
# RIGHT — gather independent reads
async def warm_cache(self, key):
    bot_data, workspace_data = await asyncio.gather(
        self._redis.get(f"bot:{key}"),
        self._redis.get(f"ws:{key}"),
    )
    return bot_data, workspace_data
```

### `asyncio.gather` without `return_exceptions=` for side effects

```python
# WRONG — H2 violation. One Redis flake kills the request path.
await asyncio.gather(
    self._redis.set(key_a, val_a),
    self._redis.publish(channel, payload),
)
```

```python
# RIGHT — side-effect failures degrade silently, errors logged
results = await asyncio.gather(
    self._redis.set(key_a, val_a),
    self._redis.publish(channel, payload),
    return_exceptions=True,
)
for r in results:
    if isinstance(r, Exception):
        log.warning("side_effect_failed", error=str(r))
```

---

## Tuning + false positives

`audit_async_mindset.sh` is heuristic — false positives expected:

- H1 flags `await self._redis.X(); if cond: await self._redis.Y()` even
  though the second await is data-dependent on the first. **Action**:
  ignore the finding if the second await reads a value derived from
  the first. The script does not gate CI in default mode for this
  reason.

- H2 flags `asyncio.gather(req_a, req_b)` where token "set" appears in
  a comment line. **Action**: rename the comment or accept the warning.

If a finding is a real false positive, leave it alone — `--strict`
mode is off by default and the warning surfaces in CI logs without
blocking the merge. Promote to `--strict` only after the baseline
list is squashed.

---

## CI integration

`.github/workflows/audit-agent-diff.yml` runs on every pull request
into `main` or any campaign branch. The job invokes:

```bash
bash scripts/audit_agent_diff.sh \
    --regression-only \
    "${{ github.event.pull_request.head.sha }}" \
    "origin/${{ github.base_ref }}"
```

`--regression-only` means a PR that does not introduce NEW violations
passes even if the base already has known issues. This unblocks
coder teams from cleaning up pre-existing legacy hits and lets them
focus on not regressing.

The async-mindset script is NOT yet wired to CI as a gate — it is run
manually during code review. Promotion path: once the baseline is at
0 H2 findings, add the script as a `--strict` gate in the same
workflow.

---

## Local pre-commit hook

To run the regression-only gate before pushing locally:

```bash
# .git/hooks/pre-push (chmod +x)
#!/bin/bash
bash scripts/audit_agent_diff.sh --regression-only \
    "$(git rev-parse HEAD)" \
    "origin/main"
```

---

## Adding a new guard

The wrapper is intentionally thin. To add a 5th guard:

1. Create `scripts/audit_<name>.sh` that exits 0 on clean, prints
   grep-style `file:lineno:reason` lines on hit.
2. Add a `Guard 5` block inside `run_guards_at()` of
   `scripts/audit_agent_diff.sh` mirroring the existing pattern
   (run helper → count violations → write to `counts.txt`).
3. Append the new tuple to the `read -r` lines and add a delta
   block.

Keep the guard idempotent (no DB / network calls); the script is
expected to run in < 10 s on a developer laptop.
