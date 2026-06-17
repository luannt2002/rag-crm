"""Admin AI config mutation schemas (Task E)."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, SecretStr


class AdminUpdateProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    type: str | None = None
    base_url: str | None = None
    auth_type: str | None = None
    credentials_vault_path: str | None = None
    enabled: bool | None = None


class RotateKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plain_key: SecretStr


class AdminUpdateModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    context_window: int | None = None
    max_output_tokens: int | None = None
    input_price_per_1k_usd: Decimal | None = None
    output_price_per_1k_usd: Decimal | None = None
    supports_streaming: bool | None = None
    supports_tools: bool | None = None
    supports_vision: bool | None = None
    supports_json_mode: bool | None = None


class AdminUpdateBindingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: bool | None = None
    rank: int | None = None
    weight: int | None = None
    variant: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None


class AddKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plain_key: SecretStr
    set_as_default: bool = False
    verify_first: bool = True  # safer default — actual API ping before commit


__all__ = [
    "AddKeyRequest",
    "AdminUpdateBindingRequest",
    "AdminUpdateModelRequest",
    "AdminUpdateProviderRequest",
    "RotateKeyRequest",
]
