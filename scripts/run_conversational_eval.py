"""Run the M26 conversational eval suites against the live bot → COVERAGE number.

Loads tests/scenarios/*_conversational_suite.json, fires each question at the
test-chat endpoint, and scores:
  - must_refuse=True  → correct when the answer is a refusal (no fabricated value).
  - must_refuse=False → correct when EVERY expected_substring (literal corpus
    value) appears in the answer, accent-insensitive, and it is not a refusal.

Reports COVERAGE per bot + per intent + the failing cases. Read-only (no DB write).

    set -a && source .env && set +a && python scripts/run_conversational_eval.py
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
import unicodedata

import httpx

BASE = os.environ.get("RAGBOT_BASE_URL", "http://localhost:3004")
# Refusal markers — generic, the shape of a "no answer / out-of-scope" reply.
_REFUSAL_MARKERS = (
    "chua thay", "khong thay", "khong co thong tin", "khong tim thay",
    "chua co thong tin", "chua co", "khong co", "khong ton tai",
    "khong quy dinh", "khong neu", "khong de cap", "khong phan phoi",
    "chua tim thay", "lien he", "hotline", "xin loi", "rat tiec",
    "khong the cung cap", "ngoai pham vi", "chi ho tro", "chi tu van",
)


def _fold(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in nfkd if unicodedata.category(c) != "Mn")


def _norm(s: str) -> str:
    """Fold accents AND collapse thousand separators so '1.098.000' == '1098000'."""
    return re.sub(r"[.,\s](?=\d)", "", _fold(s))


def _is_refusal(ans: str) -> bool:
    f = _fold(ans)
    return any(m in f for m in _REFUSAL_MARKERS)


def _token(client: httpx.Client) -> str:
    r = client.get(f"{BASE}/api/ragbot/test/tokens/self", params={"role": "owner"})
    r.raise_for_status()
    d = r.json()
    return d.get("data", {}).get("token") or d.get("token") or ""


def _ask(client: httpx.Client, token: str, suite: dict, q: dict) -> str:
    body = {
        "bot_id": suite["bot_id"],
        "channel_type": suite.get("channel_type", "web"),
        "workspace_id": suite.get("workspace_id") or suite["bot_id"],
        "question": q["question"],
        "connect_id": f"eval-{q['id']}",
        "bypass_cache": True,
    }
    r = client.post(
        f"{BASE}/api/ragbot/test/chat", json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    r.raise_for_status()
    p = r.json()
    d = p.get("data", p)
    return d.get("answer") or d.get("response") or d.get("message") or ""


def _score(q: dict, ans: str) -> bool:
    if q.get("must_refuse"):
        return _is_refusal(ans)
    if _is_refusal(ans):
        return False
    na = _norm(ans)
    return all(_norm(sub) in na for sub in q.get("expected_substrings", []))


def main() -> int:
    suites = sorted(glob.glob("tests/scenarios/*_conversational_suite.json"))
    if not suites:
        print("no conversational suites found", file=sys.stderr)
        return 1
    total = ok = 0
    by_bot: dict[str, list[int]] = {}
    by_intent: dict[str, list[int]] = {}
    fails: list[str] = []
    with httpx.Client(timeout=120) as client:
        token = _token(client)
        for path in suites:
            suite = json.load(open(path, encoding="utf-8"))
            bot = suite["bot_id"]
            by_bot.setdefault(bot, [0, 0])
            for q in suite["questions"]:
                try:
                    ans = _ask(client, token, suite, q)
                except (httpx.HTTPError, ValueError) as exc:
                    ans = f"<error: {exc}>"
                correct = _score(q, ans)
                total += 1
                ok += int(correct)
                by_bot[bot][0] += int(correct)
                by_bot[bot][1] += 1
                il = q.get("intent_label", "?")
                by_intent.setdefault(il, [0, 0])
                by_intent[il][0] += int(correct)
                by_intent[il][1] += 1
                if not correct:
                    fails.append(
                        f"  [{bot}/{il}] {q['id']}: {q['question'][:60]!r}\n"
                        f"      want={q.get('expected_substrings')} refuse={q.get('must_refuse')}\n"
                        f"      got={ans[:140]!r}"
                    )

    print("\n==================== CONVERSATIONAL COVERAGE ====================")
    print(f"OVERALL: {ok}/{total} = {ok / max(total, 1) * 100:.1f}%")
    print("\nBy bot:")
    for bot, (c, n) in sorted(by_bot.items()):
        print(f"  {bot:28} {c}/{n} = {c / max(n, 1) * 100:.0f}%")
    print("\nBy intent:")
    for il, (c, n) in sorted(by_intent.items()):
        print(f"  {il:18} {c}/{n} = {c / max(n, 1) * 100:.0f}%")
    print(f"\nFAILURES ({len(fails)}):")
    print("\n".join(fails))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
