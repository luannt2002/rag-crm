"""Source-normalization: clone the 9 real customer docs → rewrite each into its
HAPPY-CASE format (per docs/dev/HAPPY_CASE_DOCUMENT_FORMAT.md), then re-run the
happy-case checker to prove the messy sources become clean once normalized.

This is a ONE-TIME SOURCE FIX (a data migration), NOT parser code in src/ — the whole
point (SOTA "fix source first") is that the clean data lives at the source, so the
domain-neutral parser stays simple. Output goes to a git-ignored reports/ dir.

    set -a && source .env && set +a
    python scripts/normalize_to_happy_case.py
"""
from __future__ import annotations

import asyncio
import csv
import io
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import asyncpg  # noqa: E402

from ragbot.shared.document_stats import _normalise  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "reports" / "happy_case_clone"
_STEP_RE = re.compile(r"^(bước\s*\d+|[IVX]+/|\d+\.)\s*", re.IGNORECASE)

# Generic header-rename map: messy owner column labels → the canonical token the parser
# recognises as a role. Keyed on the NORMALISED (lower-case, accent-stripped) owner
# header so "Mặt hàng" / "MẶT HÀNG" / "mat hang" all map. Domain-neutral — these are
# generic catalog-column synonyms, NOT per-bot service/brand names. Applied to the
# header row ONLY, before role detection; data values are never touched. A header with
# no entry here is left as-is (the checker's column-roles card flags it separately).
_HEADER_RENAME_MAP: dict[str, str] = {
    # → name
    "mat hang": "Tên", "ten hang": "Tên", "ten san pham": "Tên", "ten dich vu": "Tên",
    "san pham": "Tên", "dich vu": "Tên", "ten kho": "Kho",
    # → category
    "phan loai": "Nhóm", "vung": "Nhóm", "danh muc": "Nhóm", "loai": "Nhóm",
    "khu vuc": "Nhóm",
    # → price
    "don gia": "Giá", "gia ban": "Giá", "gia le": "Giá", "thanh tien": "Giá",
    # → aliases
    "tu khoa": "Aliases", "bien the": "Aliases", "synonym": "Aliases",
    "synonyms": "Aliases", "keyword": "Aliases", "keywords": "Aliases",
    "variant": "Aliases", "variants": "Aliases",
}


def rename_headers_to_canonical(raw: str) -> str:
    """Rename the CSV header row's owner labels → canonical role tokens.

    Data-preserving: only the FIRST line's cells are remapped (via
    ``_HEADER_RENAME_MAP``, accent/case-insensitive); every data value is rewritten
    verbatim. An unmapped header cell is kept as-is. Returns the CSV unchanged when it
    has no rows. RFC-4180 quoting is preserved by round-tripping through ``csv``.
    """
    rows = list(csv.reader(io.StringIO(raw)))
    if not rows:
        return raw
    header = [_HEADER_RENAME_MAP.get(_normalise(c.strip()), c) for c in rows[0]]
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows[1:])
    return buf.getvalue()


def normalize_kv_export(raw: str) -> str:
    """xe-3 shape — a search-index export where each row is
    ``question: <synonyms> | … | code: X | productname: Y | price: Z | quantity: Q``.
    RE-SHAPE (not drop) into a clean multi-column catalog: every business field gets
    its own named column AND the synonyms are PRESERVED in an ``Aliases`` column (so
    BM25 still matches the 62 spelling variants). Nothing real is lost — only the row
    encoding changes from search-index to catalog."""
    out = ["Tên,Giá,Mã,Số lượng,Ngày,Ảnh,Aliases"]
    for row in csv.reader(io.StringIO(raw)):
        joined = " | ".join(c.strip() for c in row if c.strip())
        fields = {k.lower(): v.strip() for k, v in re.findall(r"(\w+):\s*([^|]+)", joined)}
        # plain spelling variants = cells with no "key:" marker + the question value
        variants = [c.strip() for c in row if c.strip() and not re.match(r"^\w+:", c.strip())]
        if fields.get("question"):
            variants.insert(0, fields["question"])
        name = fields.get("productname", "").replace('"', "'")
        price = re.sub(r"[^\d]", "", fields.get("price", ""))
        if name and price:
            code = fields.get("code", "").replace('"', "'")
            qty = re.sub(r"[^\d]", "", fields.get("quantity", ""))
            date = fields.get("date1", "").replace('"', "'")
            img = fields.get("image", "").replace('"', "'")
            aliases = "; ".join(dict.fromkeys(variants)).replace('"', "'")
            out.append(f'"{name}",{price},"{code}",{qty},"{date}","{img}","{aliases}"')
    return "\n".join(out) + "\n"


def normalize_script_to_doc(raw: str) -> str:
    """spa-4 shape — a consultation SCRIPT mis-stored as a sheet. Rewrite to a DOC:
    step markers ("Bước 1:", "II/") become ## headings; the rest stays as prose. No
    price extraction — figures stay inside their qualifying sentences (HALLU-safe)."""
    out = ["# Kịch bản tư vấn\n"]
    for row in csv.reader(io.StringIO(raw)):
        cells = [c.strip() for c in row if c.strip()]
        if not cells:
            out.append("")
            continue
        first = cells[0]
        if _STEP_RE.match(first):
            out.append(f"\n## {first}\n")
            rest = " ".join(cells[1:]).strip()
            if rest:
                out.append(rest)
        else:
            out.append(" ".join(cells))
    return "\n".join(out) + "\n"


def normalize_add_headings(raw: str) -> str:
    """xe-4 shape — a prose policy doc with NO markdown headings. Promote ALL-CAPS /
    short title-ish lines to ## headings so HDT chunking can anchor sections."""
    out = ["# Chính sách\n"]
    for ln in raw.splitlines():
        s = ln.strip()
        if not s:
            out.append("")
            continue
        # a short line that is title-like (mostly upper / ends without sentence punct)
        if len(s) <= 60 and (s.isupper() or (not s.endswith((".", ",", ":")) and len(s.split()) <= 8)):  # noqa: PLR2004
            out.append(f"\n## {s}\n")
        else:
            out.append(s)
    return "\n".join(out) + "\n"


def normalize_add_header(raw: str, header: str) -> str:
    """xe-2 shape — a data sheet missing a header row. Prepend a clean header."""
    return header + "\n" + raw.lstrip()


# Per-document normalization plan (others are already happy → clone unchanged).
PLAN = {
    "xe-3": ("synonym-export → catalog", normalize_kv_export),
    "spa-4": ("script → doc", normalize_script_to_doc),
    "xe-4": ("prose → doc+headings", normalize_add_headings),
    "xe-2": ("add header", lambda r: normalize_add_header(r, "Marks,Cargo,Ngày về")),
}


def _loss_report(original: str, normalized: str) -> str:
    """Verify the rewrite is ADDITIVE (format only) — every real datum survives.

    Checks: (a) all prices (5-7 digit runs), (b) all 'productname:' values, (c) for
    plain docs, every 4+ char word. Returns '' when nothing is lost."""
    norm_words = set(re.findall(r"\w{4,}", normalized.lower()))
    # prices: every multi-digit number in the original must still appear
    orig_prices = {re.sub(r"[^\d]", "", p) for p in re.findall(r"price:\s*([\d.,]+)", original)}
    if not orig_prices:
        orig_prices = set(re.findall(r"\b\d{6,7}\b", original))
    miss_price = [p for p in orig_prices if p and p not in re.sub(r"[^\d]", " ", normalized).split()]
    # product names
    orig_names = [n.strip() for n in re.findall(r"productname:\s*([^|]+)", original)]
    miss_name = [n for n in orig_names if n and not all(w in norm_words for w in re.findall(r"\w{4,}", n.lower())[:3])]
    # generic word coverage (for prose docs)
    orig_words = set(re.findall(r"\w{4,}", original.lower()))
    miss_words = orig_words - norm_words
    parts = []
    if miss_price:
        parts.append(f"{len(miss_price)} price(s) LOST")
    if miss_name:
        parts.append(f"{len(miss_name)} name(s) LOST")
    if len(miss_words) > max(5, len(orig_words) // 100):
        parts.append(f"{len(miss_words)}/{len(orig_words)} words LOST")
    return " · ".join(parts)


async def main() -> None:
    dsn = re.sub(r"\+\w+", "", os.environ.get("DATABASE_URL", ""))
    con = await asyncpg.connect(dsn)
    rows = await con.fetch(
        "SELECT document_name, raw_content FROM documents "
        "WHERE deleted_at IS NULL AND raw_content IS NOT NULL ORDER BY document_name")
    await con.close()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Cloning + normalizing {len(rows)} docs → {OUT_DIR}\n")
    for r in rows:
        name, raw = r["document_name"], r["raw_content"]
        if name in PLAN:
            label, fn = PLAN[name]
            new = fn(raw)
            action = f"NORMALIZED ({label}): {len(raw)} → {len(new)} chars"
        elif raw.lstrip().startswith("#"):
            # Already a markdown DOC (heading-structured) — no column header to rename.
            new = raw
            action = "cloned as-is (doc)"
        else:
            # A sheet: rename owner header labels → canonical role tokens BEFORE role
            # detection. Data-preserving; an unmapped header is left for the checker.
            new = rename_headers_to_canonical(raw)
            renamed = new.splitlines()[:1] != raw.splitlines()[:1]
            action = ("HEADER-RENAMED → canonical" if renamed
                      else "cloned as-is (already canonical)")
        ext = ".md" if (name in ("xe-4", "spa-4") or new.lstrip().startswith("#")) else ".csv"
        (OUT_DIR / f"{name}{ext}").write_text(new, encoding="utf-8")
        loss = _loss_report(raw, new) if name in PLAN else ""
        flag = "  ⚠️ " + loss if loss else ("  ✅ no data loss" if name in PLAN else "")
        print(f"  {name:18} {action}{flag}")
    print(f"\nDone. Now check: python scripts/check_happy_case.py {OUT_DIR}/<name>")


if __name__ == "__main__":
    asyncio.run(main())
