#!/usr/bin/env python3
"""vlm_spike.py — measure-first proof that gpt-4.1-mini captions images faithfully.

De-risks multimodal Phase 2 BEFORE building the ingest adapter: feeds the Phase-0
fixtures to gpt-4.1-mini via the OpenAI vision message shape (the same shape Phase 1
enabled on LLMMessage.content) and checks the EVAL_SPEC gate:
  - price_table.png  → caption MUST contain the 3 values (coverage)
  - blank_panel.png  → caption MUST contain NO price/number (HALLU trap, sacred=0)

If this passes, the model + premise are proven and Phase 2 is just wiring. If it fails,
we learn it here for one API call instead of after building the adapter.
"""
from __future__ import annotations

import asyncio
import base64
import os
import re
import sys

import litellm

_FIX = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "multimodal")
_MODEL = "gpt-4.1-mini"
_EXPECT = ["1044000", "810000", "1944000"]  # mirrors EVAL_SPEC / PRICE_TABLE_ROWS

_CAPTION_PROMPT = (
    "Mô tả chính xác nội dung bảng/ảnh dưới đây thành văn bản tiếng Việt. "
    "Liệt kê đầy đủ từng dòng sản phẩm và giá đúng như trong ảnh. "
    "TUYỆT ĐỐI KHÔNG bịa số/giá không có trong ảnh — nếu ảnh không có dữ liệu giá, "
    "nói rõ là không có thông tin giá."
)


def _norm(s: str) -> str:
    return re.sub(r"(?<=\d)[.,\s](?=\d)", "", (s or "").lower())


async def _caption(path: str) -> str:
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    resp = await litellm.acompletion(
        model=_MODEL,
        api_key=os.environ["OPENAI_API_KEY"],
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": _CAPTION_PROMPT},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }],
        temperature=0.0,
        max_tokens=400,
    )
    return resp.choices[0].message.content or ""


async def main() -> int:
    # Coverage case
    cap = await _caption(os.path.join(_FIX, "price_table.png"))
    ncap = _norm(cap)
    hits = [v for v in _EXPECT if v in ncap]
    cov_pass = len(hits) == len(_EXPECT)
    print(f"[price_table] coverage {len(hits)}/{len(_EXPECT)} {'PASS' if cov_pass else 'FAIL'}")
    print(f"  caption: {cap[:240]}")

    # HALLU trap
    blank = await _caption(os.path.join(_FIX, "blank_panel.png"))
    nblank = _norm(blank)
    invented = [v for v in _EXPECT if v in nblank]
    # any 6+ digit run in the blank caption = a fabricated price
    digit_run = re.search(r"\d{6,}", nblank)
    trap_pass = not invented and digit_run is None
    print(f"[blank_panel] HALLU-trap {'PASS' if trap_pass else 'FAIL'} "
          f"(invented={invented}, digit_run={digit_run.group() if digit_run else None})")
    print(f"  caption: {blank[:240]}")

    ok = cov_pass and trap_pass
    print(f"\nGATE: {'PASS — VLM premise proven, Phase 2 is wiring' if ok else 'FAIL — investigate before building'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
