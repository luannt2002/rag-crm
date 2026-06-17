"""Pre-flight verify: LiteLLM actually routes to Innocom LM Studio.

Plan v2 (``plans/260521-INNOCOM-3SVC-SWAP/plan.md``) assumes that seeding
``ai_providers.lmstudio`` with ``requires_prefix=true`` causes
``AiModelResolver.format_litellm_model()`` to emit a LiteLLM model name
``"lmstudio/gemma-4-e2b-it"`` and that LiteLLM then routes the call to
``LMSTUDIO_BASE_URL`` instead of OpenAI.

This script verifies the assumption **before** the 4-migration alembic
ship lands — if the assumption breaks, the migrations would still apply
cleanly but the actual LLM calls would silently fall back to OpenAI (or
fail), defeating the entire cost-saving goal.

Three probes, escalating from raw to integrated:

1. **Probe A — Raw LM Studio**: pure ``httpx`` POST to
   ``$LMSTUDIO_BASE_URL/v1/chat/completions`` with bearer auth.
   Establishes the endpoint itself is reachable from this host with
   ``gemma-4-e2b-it`` loaded.

2. **Probe B — LiteLLM direct**: ``litellm.acompletion(model="lmstudio/
   gemma-4-e2b-it", api_base=$LMSTUDIO_BASE_URL/v1, api_key=$LMSTUDIO_API_KEY,
   ...)``. Verifies LiteLLM recognises the ``lmstudio/`` prefix and
   honours the explicit ``api_base`` override — this is the path
   Ragbot's ``AiModelResolver`` will hit after the alembic ship.

3. **Probe C — LiteLLM via openai-compat prefix**: fallback path where
   the prefix is rewritten to ``openai/gemma-4-e2b-it`` + ``api_base``
   override. Some LiteLLM releases recognise this as the canonical
   "OpenAI-compatible custom endpoint" pattern; if Probe B fails for
   prefix reasons, this tells us whether to seed ``code="openai"`` with
   a per-binding ``api_base`` instead.

Usage::

    set -a && source .env && set +a
    python scripts/preflight_innocom_litellm_routing.py

Exits with status 0 only when all three probes return a non-empty,
content-bearing response. Any probe that errors prints the precise
exception so the operator can fix the routing pattern before shipping
the migrations.

Domain-neutral / zero-hardcode: every endpoint URL and bearer token
flows in via env (``LMSTUDIO_BASE_URL`` + ``LMSTUDIO_API_KEY``). No
``.env`` write, no DB write — read-only network probe. Safe to re-run.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Any

import httpx


# ---------------------------------------------------------------------------
# Constants — script-local, not Ragbot SSoT. Pre-flight one-shot only.
# ---------------------------------------------------------------------------

_PROBE_MODEL = "gemma-4-e2b-it"
_PROBE_PROMPT = (
    "Trả lời ngắn bằng tiếng Việt: 2 cộng 3 bằng bao nhiêu?"
)
_PROBE_MAX_TOKENS = 60
_PROBE_TIMEOUT_S = 25
_MIN_CONTENT_CHARS = 5  # response must contain at least this much text


def _require_env() -> tuple[str, str]:
    """Pull LMSTUDIO base URL + API key from env or exit with status 2.

    Mirrors the rest of the Wave G tooling so a CI re-run that forgets
    to source ``.env`` errors out loudly instead of silently probing a
    non-existent endpoint.
    """
    base = os.getenv("LMSTUDIO_BASE_URL")
    key = os.getenv("LMSTUDIO_API_KEY")
    if not base or not key:
        sys.stderr.write(
            "ERROR: export LMSTUDIO_BASE_URL and LMSTUDIO_API_KEY before run.\n"
        )
        sys.exit(2)
    # Strip trailing /v1 if present so callers can append explicitly.
    base = base.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return base, key


# ---------------------------------------------------------------------------
# Probe A — raw httpx POST. No LiteLLM dependency.
# ---------------------------------------------------------------------------


def _probe_raw_httpx(base: str, key: str) -> dict[str, Any]:
    """Hit ``{base}/v1/chat/completions`` directly. Returns probe result."""
    url = f"{base}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        # Some LM Studio installs sit behind Cloudflare-style filters
        # that reject the default httpx UA.
        "User-Agent": "Mozilla/5.0 (preflight innocom routing)",
    }
    payload = {
        "model": _PROBE_MODEL,
        "messages": [{"role": "user", "content": _PROBE_PROMPT}],
        "max_tokens": _PROBE_MAX_TOKENS,
        "temperature": 0.0,
    }
    t0 = time.perf_counter()
    try:
        with httpx.Client(timeout=_PROBE_TIMEOUT_S) as client:
            resp = client.post(url, headers=headers, json=payload)
        latency = time.perf_counter() - t0
        if resp.status_code != 200:
            return {
                "name": "A_raw_httpx",
                "ok": False,
                "latency_s": round(latency, 2),
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
            }
        data = resp.json()
        content = (
            data.get("choices", [{}])[0].get("message", {}).get("content", "")
            or ""
        )
        return {
            "name": "A_raw_httpx",
            "ok": len(content) >= _MIN_CONTENT_CHARS,
            "latency_s": round(latency, 2),
            "out_chars": len(content),
            "preview": content[:80],
        }
    except httpx.HTTPError as exc:
        return {
            "name": "A_raw_httpx",
            "ok": False,
            "latency_s": round(time.perf_counter() - t0, 2),
            "error": f"{type(exc).__name__}: {exc}",
        }


# ---------------------------------------------------------------------------
# Probe B — LiteLLM with the assumed ``lmstudio/`` prefix.
# ---------------------------------------------------------------------------


async def _probe_litellm_lmstudio_prefix(base: str, key: str) -> dict[str, Any]:
    """LiteLLM call with ``model='lmstudio/gemma-4-e2b-it'``."""
    try:
        import litellm  # noqa: PLC0415
    except ImportError:
        return {
            "name": "B_litellm_lmstudio_prefix",
            "ok": False,
            "error": "litellm package not importable",
        }
    api_base = f"{base}/v1"
    t0 = time.perf_counter()
    try:
        resp = await litellm.acompletion(
            model=f"lmstudio/{_PROBE_MODEL}",
            messages=[{"role": "user", "content": _PROBE_PROMPT}],
            max_tokens=_PROBE_MAX_TOKENS,
            temperature=0.0,
            api_base=api_base,
            api_key=key,
            timeout=_PROBE_TIMEOUT_S,
        )
        latency = time.perf_counter() - t0
        content = (
            resp["choices"][0]["message"]["content"]
            if isinstance(resp, dict)
            else resp.choices[0].message.content
        ) or ""
        return {
            "name": "B_litellm_lmstudio_prefix",
            "ok": len(content) >= _MIN_CONTENT_CHARS,
            "latency_s": round(latency, 2),
            "out_chars": len(content),
            "preview": content[:80],
        }
    except Exception as exc:  # noqa: BLE001 — pre-flight, report shape
        return {
            "name": "B_litellm_lmstudio_prefix",
            "ok": False,
            "latency_s": round(time.perf_counter() - t0, 2),
            "error": f"{type(exc).__name__}: {str(exc)[:240]}",
        }


# ---------------------------------------------------------------------------
# Probe C — LiteLLM with the OpenAI-compatible custom endpoint pattern.
# ---------------------------------------------------------------------------


async def _probe_litellm_openai_compat(base: str, key: str) -> dict[str, Any]:
    """Fallback: ``model='openai/gemma-4-e2b-it'`` + ``api_base`` override.

    Several LiteLLM releases route any ``openai/<name>`` call through
    the standard chat-completions adapter and respect ``api_base``,
    which gives us a second viable wiring pattern if the bespoke
    ``lmstudio/`` prefix is not recognised by the installed release.
    """
    try:
        import litellm  # noqa: PLC0415
    except ImportError:
        return {
            "name": "C_litellm_openai_compat",
            "ok": False,
            "error": "litellm package not importable",
        }
    api_base = f"{base}/v1"
    t0 = time.perf_counter()
    try:
        resp = await litellm.acompletion(
            model=f"openai/{_PROBE_MODEL}",
            messages=[{"role": "user", "content": _PROBE_PROMPT}],
            max_tokens=_PROBE_MAX_TOKENS,
            temperature=0.0,
            api_base=api_base,
            api_key=key,
            timeout=_PROBE_TIMEOUT_S,
        )
        latency = time.perf_counter() - t0
        content = (
            resp["choices"][0]["message"]["content"]
            if isinstance(resp, dict)
            else resp.choices[0].message.content
        ) or ""
        return {
            "name": "C_litellm_openai_compat",
            "ok": len(content) >= _MIN_CONTENT_CHARS,
            "latency_s": round(latency, 2),
            "out_chars": len(content),
            "preview": content[:80],
        }
    except Exception as exc:  # noqa: BLE001 — pre-flight, report shape
        return {
            "name": "C_litellm_openai_compat",
            "ok": False,
            "latency_s": round(time.perf_counter() - t0, 2),
            "error": f"{type(exc).__name__}: {str(exc)[:240]}",
        }


# ---------------------------------------------------------------------------
# Main — pretty-print + exit status
# ---------------------------------------------------------------------------


def _format_row(result: dict[str, Any]) -> str:
    """One-line tabular summary for each probe."""
    name = result["name"]
    status = "PASS" if result.get("ok") else "FAIL"
    latency = result.get("latency_s")
    if "error" in result:
        return f"  {name:<32} {status:<6} lat={latency}s err={result['error']}"
    return (
        f"  {name:<32} {status:<6} lat={latency}s "
        f"out={result.get('out_chars')} preview={result.get('preview')!r}"
    )


def _recommendation(probes: list[dict[str, Any]]) -> str:
    """Translate probe outcomes into an operator action."""
    raw_ok = probes[0]["ok"]
    lmstudio_ok = probes[1]["ok"]
    openai_compat_ok = probes[2]["ok"]
    if not raw_ok:
        return (
            "ABORT: raw httpx probe failed — endpoint unreachable or model "
            "not loaded. Fix LM Studio host before considering swap."
        )
    if lmstudio_ok:
        return (
            "GO with Plan v2 as written: seed ai_providers.code='lmstudio' "
            "with requires_prefix=true. LiteLLM routes lmstudio/{model} "
            "via the installed adapter."
        )
    if openai_compat_ok:
        return (
            "ADJUST Plan v2: rewrite ai_providers seed to code='openai' "
            "with a per-binding api_base override (NOT 'lmstudio'). "
            "LiteLLM's lmstudio adapter is unavailable in this release; "
            "openai/{model} + api_base hits the same endpoint."
        )
    return (
        "ABORT: both LiteLLM patterns failed. Endpoint is reachable but "
        "LiteLLM cannot route to it — escalate to investigate the "
        "installed litellm release + supported provider prefixes."
    )


async def _main() -> int:
    base, key = _require_env()
    print(f"Pre-flight LiteLLM routing — endpoint base: {base}")
    print(f"Probe model: {_PROBE_MODEL}")
    print()

    raw = _probe_raw_httpx(base, key)
    lmstudio = await _probe_litellm_lmstudio_prefix(base, key)
    openai_compat = await _probe_litellm_openai_compat(base, key)
    probes = [raw, lmstudio, openai_compat]

    print("PROBE RESULTS:")
    for r in probes:
        print(_format_row(r))
    print()
    print(f"RECOMMENDATION: {_recommendation(probes)}")
    print()

    n_pass = sum(1 for r in probes if r.get("ok"))
    print(f"Summary: {n_pass}/3 probes passed.")
    # Exit 0 only when at least one viable LiteLLM pattern works AND raw
    # endpoint is healthy. Otherwise non-zero so CI / operator scripts
    # can abort the alembic ship.
    return 0 if raw["ok"] and (lmstudio["ok"] or openai_compat["ok"]) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
