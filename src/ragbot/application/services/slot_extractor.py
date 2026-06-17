"""SlotExtractor — parse user message into structured slots via LLM JSON mode.

Uses ``structured_output_helper.call_with_schema`` (verified J1) with
dynamically-built Pydantic model from owner-declared slot_schema.

Sacred-rule alignment:
- Domain-neutral: slot_schema declared by bot owner via
  ``bots.action_config.slots_schema`` JSONB. This service reads schema
  + builds Pydantic model dynamically; no hardcoded slot names.
- Zero-hardcode: extractor LiteLLM model name + provider resolved from
  ``system_config.slot_extractor_model`` (default ``"anthropic/claude-haiku-4-5"``
  per memory ``feedback_haiku_partial_only``). Tier policy honoured.
- HALLU=0 at boundary: Pydantic validates slot types; LLM JSON strict
  mode forbids fabricated keys; missing required slots map to
  ``None`` (caller decides which slot to ask user for).
- Multi-tenant: schema per-bot; service is stateless per-call.
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog
from pydantic import BaseModel, Field, ValidationError, create_model

from ragbot.application.services.structured_output_helper import call_with_schema
from ragbot.shared.constants import (
    DEFAULT_SLOT_EXTRACTOR_MAX_TOKENS,
    DEFAULT_SLOT_EXTRACTOR_MODEL_WIRE,
    DEFAULT_SLOT_EXTRACTOR_PROVIDER,
    DEFAULT_SLOT_EXTRACTOR_SONNET_WIRE,
)

logger = structlog.get_logger(__name__)


_EXTRACT_SYSTEM_PROMPT = """Bạn là slot extractor cho hệ thống chatbot tư vấn.

Nhiệm vụ: parse câu user gửi → trích xuất slots theo schema JSON cung cấp.

QUY TẮC NGHIÊM NGẶT:
1. CHỈ extract slots literal trong message user. KHÔNG suy diễn / fabricate.
2. Slot không tìm thấy trong message → trả null (không phải empty string).
3. Trả về JSON theo schema cung cấp, KHÔNG thêm key thừa.
4. KHÔNG dịch / paraphrase giá trị slot — giữ nguyên text user."""


# Default LiteLLM wire name when system_config key missing — from constants
# (SSoT). Haiku is correct tier for token-small JSON extraction per
# memory ``feedback_haiku_partial_only``.
_DEFAULT_MODEL_WIRE = DEFAULT_SLOT_EXTRACTOR_MODEL_WIRE
_DEFAULT_PROVIDER_CODE = DEFAULT_SLOT_EXTRACTOR_PROVIDER


class SlotExtractor:
    """Extract structured slots from a single user turn via LLM JSON mode.

    Constructor injection:
      - ``litellm_module``: LiteLLM module (production: ``import litellm``)
      - ``config_service``: ``SystemConfigService`` for model name lookup
    """

    def __init__(self, *, litellm_module: Any, config_service: Any) -> None:
        self._litellm = litellm_module
        self._cfg = config_service

    async def extract(
        self,
        *,
        user_message: str,
        slot_schema: dict[str, Any],
        intent: str | None = None,
    ) -> dict[str, Any]:
        """Extract slots from ``user_message`` per ``slot_schema``.

        @param user_message: raw user text (current turn only)
        @param slot_schema: per-bot schema from ``bots.action_config.slots_schema``
            Example: {"booking": {"required": ["service", "name", "phone",
            "datetime"], "optional": []}}
        @param intent: optional intent label to pick which sub-schema
        @return: dict of extracted slots; missing slots = None (filtered out)
        """
        if not user_message or not slot_schema:
            return {}

        # Pick sub-schema by intent (e.g. booking) — default first key
        sub_schema_key = intent if intent and intent in slot_schema else next(iter(slot_schema), None)
        if not sub_schema_key:
            return {}
        sub_schema = slot_schema.get(sub_schema_key, {})
        # Normalize to a uniform field list — accepts BOTH the new owner format
        # ``{"fields": [{"key","label","desc","type","required"}]}`` AND the
        # legacy ``{"required": [...names...], "optional": [...]}``.
        fields = self._normalize_fields(sub_schema)
        if not fields:
            return {}

        # Build Pydantic model dynamically (field desc → JSON-schema description
        # so the LLM knows what each custom field means).
        try:
            SchemaModel = self._build_pydantic_model(sub_schema_key, fields)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "slot_extractor_schema_build_failed",
                schema=sub_schema_key,
                error=str(exc),
            )
            return {}

        # Resolve model from system_config
        litellm_name, provider_code = await self._resolve_model()

        # Compose prompt — one line per field with its meaning so a custom
        # business field (e.g. loan_amount, cccd, address) is extracted correctly.
        field_lines = []
        for f in fields:
            req = "bắt buộc" if f["required"] else "tùy chọn"
            meaning = f' — {f["desc"]}' if f["desc"] and f["desc"] != f["key"] else ""
            type_hint = f' [{f["type"]}]' if f.get("type") and f["type"] != "text" else ""
            field_lines.append(f'- "{f["key"]}"{type_hint} ({req}){meaning}')
        user_prompt = (
            "Schema: trích xuất các slot sau từ message user. "
            "Mỗi slot kèm ý nghĩa để hiểu đúng cần trích gì.\n"
            + "\n".join(field_lines)
            + f"\n\nUser message: {user_message}\n\n"
            "Trả về JSON object với đúng các key trên. Slot không có trong message → null."
        )
        messages = [
            {"role": "system", "content": _EXTRACT_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        # Call LLM via existing helper (J1-verified, OpenAI strict mode + Anthropic tool_choice)
        try:
            parsed = await call_with_schema(
                litellm_module=self._litellm,
                litellm_name=litellm_name,
                provider_code=provider_code,
                messages=messages,
                schema=SchemaModel,
                temperature=0.0,
                max_tokens=DEFAULT_SLOT_EXTRACTOR_MAX_TOKENS,
                fallback_to_json_parse=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "slot_extractor_llm_failed",
                model=litellm_name,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return {}

        if parsed is None:
            return {}

        try:
            return self._scrub_dict(parsed.model_dump())
        except Exception as exc:  # noqa: BLE001
            logger.debug("slot_extractor_dump_failed", error=str(exc))
            return {}

    # ---------------------------------------------------------------------- #
    # Helpers                                                                 #
    # ---------------------------------------------------------------------- #
    async def _resolve_model(self) -> tuple[str, str]:
        """Resolve LiteLLM wire name + provider code from system_config."""
        try:
            alias = await self._cfg.get("slot_extractor_model", "haiku")
        except Exception:  # noqa: BLE001
            alias = "haiku"
        alias_str = str(alias).strip().strip('"').lower() or "haiku"
        # Alias → wire name mapping (kept simple; production resolver wires via DB)
        if alias_str in ("haiku",):
            return _DEFAULT_MODEL_WIRE, _DEFAULT_PROVIDER_CODE
        if alias_str in ("sonnet",):
            return DEFAULT_SLOT_EXTRACTOR_SONNET_WIRE, DEFAULT_SLOT_EXTRACTOR_PROVIDER
        if alias_str.startswith("anthropic/") or "/" in alias_str:
            return alias_str, alias_str.split("/", 1)[0]
        # Treat unknown as direct wire name with anthropic provider
        return alias_str, _DEFAULT_PROVIDER_CODE

    @staticmethod
    def _normalize_fields(sub_schema: dict[str, Any]) -> list[dict[str, Any]]:
        """Normalize either schema shape into ``[{key,label,desc,type,required}]``.

        New (owner self-service): ``{"fields": [{"key","label","desc","type",
        "required"}, ...]}``. Legacy: ``{"required": [...], "optional": [...]}``
        (names only) → desc defaults to key, type to ``"text"``.
        """
        out: list[dict[str, Any]] = []
        seen: set[str] = set()

        def _add(key: str, *, required: bool, label: str = "", desc: str = "", type_: str = "text") -> None:
            k = str(key or "").strip()
            if not k or k in seen:
                return
            seen.add(k)
            out.append({
                "key": k,
                "label": str(label or k).strip(),
                "desc": str(desc or "").strip(),
                "type": str(type_ or "text").strip().lower() or "text",
                "required": bool(required),
            })

        raw_fields = sub_schema.get("fields")
        if isinstance(raw_fields, list) and raw_fields:
            for f in raw_fields:
                if isinstance(f, dict) and f.get("key"):
                    _add(
                        f["key"],
                        required=bool(f.get("required", False)),
                        label=f.get("label", ""),
                        desc=f.get("desc", "") or f.get("description", ""),
                        type_=f.get("type", "text"),
                    )
                elif isinstance(f, str):
                    _add(f, required=False)
            return out

        # Legacy names-only format
        for name in sub_schema.get("required", []) or []:
            _add(name, required=True)
        for name in sub_schema.get("optional", []) or []:
            _add(name, required=False)
        return out

    @staticmethod
    def _build_pydantic_model(
        name: str, fields: list[dict[str, Any]],
    ) -> type[BaseModel]:
        """Build Pydantic model from normalized fields.

        Each slot is ``Optional[str]`` default None; the field ``desc`` becomes
        the JSON-schema ``description`` so the LLM understands custom fields.
        ``extra="forbid"`` rejects fabricated keys.
        """
        model_fields: dict[str, Any] = {}
        for f in fields:
            # Tolerate a plain string (legacy: name only) or a normalized dict.
            if isinstance(f, str):
                f = {"key": f, "desc": "", "label": f}
            safe_name = re.sub(r"[^A-Za-z0-9_]", "_", str(f["key"]).strip())
            if not safe_name or not safe_name[0].isalpha():
                safe_name = f"slot_{safe_name}"
            model_fields[safe_name] = (
                str | None,
                Field(default=None, description=f.get("desc") or f.get("label") or ""),
            )
        return create_model(
            f"SlotSchema_{name}",
            __config__={"extra": "forbid"},
            **model_fields,
        )

    @staticmethod
    def _scrub_dict(d: dict[str, Any]) -> dict[str, Any]:
        """Drop None and empty-string values for cleaner state."""
        return {
            k: v for k, v in d.items()
            if v is not None and (not isinstance(v, str) or v.strip())
        }


__all__ = ["SlotExtractor"]
