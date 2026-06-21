#!/usr/bin/env python3
"""gen_multimodal_fixture.py — deterministic image fixtures for the VLM eval gate.

The multimodal plan's Phase 0 needs images with KNOWN content to gate VLM captioning
(coverage) + a no-data image to gate faithfulness (HALLU trap: a VLM must not invent
values that aren't in the image). No binary media fixtures exist under tests/ today.

Produces, under tests/fixtures/multimodal/:
  price_table.png  — a clean 3-row price table; the caption MUST surface these values.
  blank_panel.png  — a plain coloured panel with NO data; the caption MUST NOT invent
                     any number/price (HALLU trap).

Deterministic (fixed text, fixed layout) — re-running yields byte-stable images so the
eval ground-truth never drifts.
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

_OUT = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "multimodal")
_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# Ground-truth the caption must reproduce (kept here so the eval imports one source).
PRICE_TABLE_ROWS = [
    ("Lop Landspider 205/55R16", "1.044.000d"),
    ("Lop Rovelo 185/65R15", "810.000d"),
    ("Lop CITYTRAXX H/T", "1.944.000d"),
]
PRICE_TABLE_TITLE = "BANG GIA LOP XE"


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def _price_table() -> Image.Image:
    w, h = 720, 360
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    title_f = _font(_FONT_BOLD, 34)
    cell_f = _font(_FONT, 26)
    d.text((30, 24), PRICE_TABLE_TITLE, fill="black", font=title_f)
    # header
    y = 90
    d.line((20, y - 8, w - 20, y - 8), fill="black", width=2)
    d.text((30, y), "San pham", fill="black", font=_font(_FONT_BOLD, 26))
    d.text((480, y), "Gia", fill="black", font=_font(_FONT_BOLD, 26))
    y += 44
    d.line((20, y - 6, w - 20, y - 6), fill="black", width=1)
    for name, price in PRICE_TABLE_ROWS:
        d.text((30, y), name, fill="black", font=cell_f)
        d.text((480, y), price, fill="black", font=cell_f)
        y += 50
    d.rectangle((10, 10, w - 10, h - 10), outline="black", width=2)
    return img


def _blank_panel() -> Image.Image:
    # No data at all — a flat panel. A faithful VLM caption must NOT state any price
    # or product (HALLU trap). Only a neutral colour band + one non-numeric word.
    w, h = 720, 360
    img = Image.new("RGB", (w, h), (210, 224, 238))
    d = ImageDraw.Draw(img)
    d.rectangle((40, 40, w - 40, h - 40), fill=(170, 196, 222))
    d.text((60, 160), "banner", fill=(120, 140, 160), font=_font(_FONT, 28))
    return img


def main() -> int:
    os.makedirs(_OUT, exist_ok=True)
    _price_table().save(os.path.join(_OUT, "price_table.png"))
    _blank_panel().save(os.path.join(_OUT, "blank_panel.png"))
    print(f"wrote price_table.png + blank_panel.png -> {os.path.normpath(_OUT)}")
    print("ground-truth (price_table):", PRICE_TABLE_ROWS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
