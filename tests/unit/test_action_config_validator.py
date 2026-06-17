"""Phase G — action_config slot-schema validator (owner self-service gate)."""
from __future__ import annotations

import pytest

from ragbot.application.services.action_config_validator import (
    ActionConfigValidationError,
    validate_action_config,
)


def _cfg(fields):
    return {"enabled": True, "slots_schema": {"booking": {"fields": fields}}}


class TestValidateActionConfig:
    def test_valid_spa_5_fields(self):
        out = validate_action_config(_cfg([
            {"key": "ten", "label": "Tên", "type": "text", "required": True},
            {"key": "sdt", "label": "SĐT", "type": "phone", "required": True},
            {"key": "khung_gio", "label": "Khung giờ", "type": "time", "required": True},
            {"key": "so_nguoi", "label": "Số người", "type": "number", "required": False},
            {"key": "ngay", "label": "Ngày", "type": "date", "required": True},
        ]))
        assert out["enabled"] is True
        assert len(out["slots_schema"]["booking"]["fields"]) == 5
        assert out["slots_schema"]["booking"]["fields"][0]["key"] == "ten"

    def test_over_5_fields_rejected(self):
        with pytest.raises(ActionConfigValidationError, match="max 5"):
            validate_action_config(_cfg([
                {"key": f"f{i}", "type": "text"} for i in range(6)
            ]))

    def test_invalid_key_rejected(self):
        with pytest.raises(ActionConfigValidationError, match="invalid field key"):
            validate_action_config(_cfg([{"key": "1bad", "type": "text"}]))
        with pytest.raises(ActionConfigValidationError, match="invalid field key"):
            validate_action_config(_cfg([{"key": "has space", "type": "text"}]))

    def test_duplicate_key_rejected(self):
        with pytest.raises(ActionConfigValidationError, match="duplicate"):
            validate_action_config(_cfg([
                {"key": "ten", "type": "text"}, {"key": "ten", "type": "text"},
            ]))

    def test_bad_type_rejected(self):
        with pytest.raises(ActionConfigValidationError, match="not in"):
            validate_action_config(_cfg([{"key": "x", "type": "wizardry"}]))

    def test_empty_fields_rejected(self):
        with pytest.raises(ActionConfigValidationError, match="non-empty"):
            validate_action_config(_cfg([]))

    def test_normalizes_defaults(self):
        out = validate_action_config(_cfg([{"key": "dia_chi"}]))
        f = out["slots_schema"]["booking"]["fields"][0]
        assert f["label"] == "dia_chi"   # label defaults to key
        assert f["type"] == "text"        # type defaults to text
        assert f["required"] is False
        assert f["desc"] == ""

    def test_non_dict_rejected(self):
        with pytest.raises(ActionConfigValidationError):
            validate_action_config("nope")

    def test_xe_5_fields_distinct_business(self):
        # Domain-neutral: a totally different business (tire shop) works too.
        out = validate_action_config(_cfg([
            {"key": "ten", "type": "text", "required": True},
            {"key": "sdt", "type": "phone", "required": True},
            {"key": "dia_chi", "type": "text", "required": True},
            {"key": "loai_xe", "label": "Xe gì", "type": "text", "required": True},
            {"key": "loai_lop", "label": "Mẫu lốp", "type": "text", "required": True},
        ]))
        assert len(out["slots_schema"]["booking"]["fields"]) == 5
