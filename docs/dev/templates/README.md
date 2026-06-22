# Golden Templates — happy-case document formats (the input CONTRACT)

> These are the **reference formats**. A document that conforms to one of these is
> guaranteed to be parsed by the L1→L7 pipeline with **0 errors / 100% coverage** —
> locked by `tests/unit/test_happy_case_template.py`. Customers copy a template,
> fill in their data, and the platform controls it expertly.
>
> Spec + anti-patterns: [../HAPPY_CASE_DOCUMENT_FORMAT.md](../HAPPY_CASE_DOCUMENT_FORMAT.md).
> Lint any file: `python scripts/check_happy_case.py <file>`.

| Template | For | Guarantee |
|---|---|---|
| `catalog_single.csv` | one price list, one table | every row → `(name, price)` entity, 100% coverage |
| `catalog_multisection.csv` | many sub-tables in one sheet | every row → entity bound to its `## section` (B3) |
| `document.md` | prose docs (policy, legal, SOP, contract) | every `##` → retrievable section; tables atomic |

## The control guarantee

```
conforms to template  ──►  L1→L7  ──►  100% coverage, 0 anomalies   (test-locked)
violates template     ──►  checker flags it + tells the customer how to fix the source
```

We do NOT try to parse non-conforming data (SOTA: "fix source first"). The checker is
the gate; the template is the target. This keeps the parser simple + domain-neutral and
makes ingestion **deterministic** — no guessing, no silent data loss.
