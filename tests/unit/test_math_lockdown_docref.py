"""Math-lockdown document-citation grounding tests.

A small answer model fabricates legal citation numbers ("NN/YYYY") from
parametric memory — e.g. it wrote "thay thế Thông tư 16/2018" when the corpus
says 18/2018. The grounding check must treat citation numbers like any other
numeric claim: flag the ones absent from every retrieved chunk. Dates
(dd/mm/yyyy) must NOT be mis-read as citation numbers (their format varies and
would cause false positives).
"""
from __future__ import annotations

from ragbot.infrastructure.guardrails.math_lockdown import (
    extract_numeric_claims,
    find_ungrounded_numbers,
)


def test_docref_extracted_as_claim() -> None:
    claims = extract_numeric_claims("thay thế Thông tư 18/2018/TT-NHNN")
    assert ("18/2018", "docref") in claims


def test_docref_leading_zero_normalised() -> None:
    # "01/2018" and "1/2018" must normalise to the same identifier.
    assert extract_numeric_claims("số 01/2018") == extract_numeric_claims("số 1/2018")


def test_fabricated_citation_flagged_ungrounded() -> None:
    answer = "Thông tư 09/2020 thay thế Thông tư số 16/2018/TT-NHNN."
    corpus = ["Thông tư này thay thế Thông tư 18/2018/TT-NHNN ngày 21/08/2018."]
    ungrounded = find_ungrounded_numbers(answer, corpus)
    vals = {u["value"] for u in ungrounded}
    assert "16/2018" in vals  # the fabricated citation is caught


def test_grounded_citation_not_flagged() -> None:
    answer = "Thông tư này thay thế Thông tư 18/2018/TT-NHNN."
    corpus = ["...thay thế Thông tư 18/2018/TT-NHNN ngày 21/08/2018..."]
    ungrounded = find_ungrounded_numbers(answer, corpus)
    assert all(u["value"] != "18/2018" for u in ungrounded)


def test_date_not_misread_as_docref() -> None:
    # A dd/mm/yyyy date must not be extracted as an NN/YYYY citation number.
    claims = extract_numeric_claims("có hiệu lực kể từ ngày 01/01/2022")
    assert not any(unit == "docref" for _, unit in claims)
