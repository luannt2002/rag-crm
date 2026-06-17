# Test isolation guide — pollution patterns + fix templates

Pytest runs all tests in a single Python process by default. Module-level
state (env vars, singletons, monkey-patched stdlib, captured stdout) leaks
across tests unless every mutation is paired with an explicit teardown.
This guide catalogues the patterns we have hit and the surgical fix for
each.

The audit baseline (`reports/TEST_FAIL_AUDIT_20260506.md`) showed 112
full-suite failures vs 0 isolated failures — every fail in suite passed
when re-run alone. That ratio is the canonical signature of pollution.

---

## Pattern 1 — third-party library writes env on import

**Symptom**: `@pytest.mark.skipif(not os.getenv("X"))` does not skip when
`X` was not exported in the shell. The test runs and either hits a real
network call, or fails on a stale credential.

**Root cause**: `litellm/__init__.py` calls `dotenv.load_dotenv()` at
import. `load_dotenv` walks parent directories until it finds any `.env`,
including `.env` files belonging to unrelated projects on the same dev
machine. Any test file that imports `litellm` re-triggers the leak. A
session-scoped fixture is too late: `skipif` evaluates at decoration time
during test-file collection.

**Fix template — defuse at conftest module top**

```python
# tests/conftest.py — runs before any test file is imported.
import os

for _leaked_key in ("OPENAI_API_KEY",):
    os.environ.pop(_leaked_key, None)
del _leaked_key

try:
    import dotenv as _dotenv
    _dotenv.load_dotenv = lambda *_a, **_kw: False
    _dotenv.find_dotenv = lambda *_a, **_kw: ""
    del _dotenv
except ImportError:
    pass
```

**Why module-top, not a fixture**: pytest collects test files (and their
imports) before any fixture runs. By the time a session fixture fires,
the env is already polluted and decorator-time `skipif` predicates have
already cached `True`.

---

## Pattern 2 — script imports DB / network at module-load

**Symptom**: ERROR (not FAIL) at fixture setup — `RuntimeError:
DATABASE_URL required to resolve …`. The error fires regardless of which
test in the file is run.

**Root cause**: a helper script (`scripts/audit_harness_run.py`) reads
config from Postgres at module top:

```python
JUDGE_MODEL = _load_judge_model_from_system_config()  # raises without DB
```

A unit test loaded that script via `importlib.util.spec_from_file_location`
to test one of its functions. `spec.loader.exec_module(mod)` executes the
top-level statements, including the DB read.

**Fix template — patch DB before exec_module, restore after**

```python
def _load_audit_mod():
    spec = importlib.util.spec_from_file_location("audit_harness_run", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["audit_harness_run"] = mod
    _patch_db_for_module_load()
    try:
        spec.loader.exec_module(mod)
    finally:
        _unpatch_db()
    return mod


def _patch_db_for_module_load() -> None:
    import psycopg2
    _orig_connect = psycopg2.connect
    psycopg2.connect = lambda *_a, **_k: _StubConn()
    _PATCHES.append((psycopg2, _orig_connect))
    _ENV_PREV.append(("DATABASE_URL", os.environ.get("DATABASE_URL")))
    os.environ["DATABASE_URL"] = "postgresql://stub:stub@stub/stub"


def _unpatch_db() -> None:
    while _PATCHES:
        mod, orig = _PATCHES.pop()
        mod.connect = orig
    while _ENV_PREV:
        key, prev = _ENV_PREV.pop()
        if prev is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prev
```

**Scope**: keep the fixture `function`-scoped, not `module`. A
module-scoped fixture that errors on first call masks all subsequent
tests in the file with the same error — function scope makes each
test's setup independent.

---

## Pattern 3 — `os.environ[K] = V` instead of `monkeypatch.setenv`

**Symptom**: tests pass alone, fail when paired with later tests that
read the same env key. The mutation persists across test boundaries.

**Wrong**:
```python
def test_foo():
    os.environ["FEATURE_X"] = "true"  # never restored
    ...
```

**Right**:
```python
def test_foo(monkeypatch):
    monkeypatch.setenv("FEATURE_X", "true")  # auto-restored at teardown
    ...
```

`monkeypatch` is function-scoped and unwinds every `setenv` / `setattr`
/ `delenv` at test exit. `os.environ[K] = V` does not.

**Already-allowed alternatives**: `with patch.dict(os.environ, {...},
clear=False):` is OK because the context manager restores on exit. So
is `with patch.dict("os.environ", {}, clear=True):`.

---

## Pattern 4 — `@lru_cache` on settings / config

**Symptom**: a test that calls `monkeypatch.setenv("APP_X", "true")` and
expects `get_settings().app_x is True` — but reads back the previous
value. The cached settings instance was populated by an earlier test.

**Root cause**: `pydantic-settings` reads env vars once at construction;
`@lru_cache` keeps the instance alive. `monkeypatch.setenv` does NOT
invalidate the cache.

**Fix template — autouse cache-clear**

```python
# tests/conftest.py
@pytest.fixture(autouse=True)
def _reset_module_singletons():
    yield
    try:
        from ragbot.config.settings import get_settings
        get_settings.cache_clear()
    except Exception:
        pass
```

For tests that mutate settings WITHIN a test body (not just before),
clear before AND after:

```python
get_settings.cache_clear()  # ensure new env is honoured
yield
get_settings.cache_clear()  # leave clean for next test
```

---

## Pattern 5 — `MagicMock` on async surface

**Symptom**: `TypeError: 'MagicMock' object can't be awaited`. Not
strictly a pollution pattern but produces the same "fails in suite, not
alone" signature when the awaited surface is set in a fixture that runs
in some test orderings but not others.

**Wrong**:
```python
llm = MagicMock()
llm.acompletion = MagicMock(return_value=fake)
await llm.acompletion()  # TypeError
```

**Right**:
```python
from unittest.mock import AsyncMock
llm = MagicMock()
llm.acompletion = AsyncMock(return_value=fake)
await llm.acompletion()  # OK
```

If the production code does `await getattr(obj, "x", None)` and the test
sets `obj = MagicMock()`, the `getattr` returns a MagicMock for ANY
attribute — including those production code expects to be `None` /
unset. Either provide an `AsyncMock` for known async attrs, or use
`spec=` on the `MagicMock` so unknown attrs raise `AttributeError`.

---

## Verification checklist

Before claiming a pollution-fix is complete, run:

```bash
# 1. Full unit suite passes.
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/ -q

# 2. Each touched test passes when run alone.
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/<file>.py -v

# 3. Re-run the suite a second time in the same process (catches state
#    that leaks between repeats — rare but real).
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/ -q --count=2  # if pytest-repeat
```

The `--runxfail` flag promotes every xfail-listed item to a regular
test, surfacing any pollution that the xfail list is masking. If a
test passes both alone and under `--runxfail` in suite, remove its
entry from `tests/_xfail_list.txt`.
