"""M0-1: DSI entity-noise filter — reject extraction artefacts, KEEP real names.

The spa stats index held ``Hiện tại`` x20 with fabricated prices (99k–499k): a
consultation-script sentence opener mis-split into a catalog row — a live HALLU
risk (a price lookup would invent a "Hiện tại = 299k" service). Alongside it,
leaked ``<chunk_context>`` tags and section/step headings ("II/ …", "Bước 1: …")
were extracted as entities.

These are rejected by SHAPE — tag-lead, roman/step enumeration lead, and
grammar discourse/temporal openers — all domain-neutral. Crucially, real short
names INCLUDING all-caps service codes (``IPL``/``VIP``) are KEPT: there is no
short-code rule (the rejected ``^[A-Z/+]{2,5}$`` shape that false-dropped them).
"""
from ragbot.shared.document_stats import _extract_entity_from_row


def _name(raw: str) -> str | None:
    e = _extract_entity_from_row([raw, "299000"], ["Tên", "Giá"], 0, None)
    return e.name if e else None


def test_discourse_temporal_opener_rejected() -> None:
    # the exact P0 row + a clause-opener sibling
    assert _name("Hiện tại") is None
    assert _name("Khi đến với spa") is None


def test_leaked_tag_lead_rejected() -> None:
    assert _name("<chunk_context>Bảng giá triệt lông") is None


def test_section_and_step_heading_rejected() -> None:
    assert _name("II/ Khách quan tâm triệt lông") is None
    assert _name("Bước 1: Chào khách") is None


def test_real_service_names_kept() -> None:
    assert _name("Mặt") == "Mặt"
    assert _name("Triệt lông toàn thân") == "Triệt lông toàn thân"
    # all-caps short service/package codes MUST survive (no short-code false-drop)
    assert _name("IPL") == "IPL"
    assert _name("VIP") == "VIP"
