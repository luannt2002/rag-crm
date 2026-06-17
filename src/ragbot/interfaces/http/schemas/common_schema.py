"""Common HTTP schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ErrorPayloadSchema(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: Literal[False] = False
    data: None = None
    error: ErrorPayloadSchema
    trace_id: str


class AcceptedResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: Literal[True] = True
    job_id: str
    status: Literal["queued"] = "queued"
    status_url: str
    trace_id: str


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "down"]
    version: str
    dependencies: dict[str, str]
    # Pool stats per dependency — keys like "db_in_use", "db_idle",
    # "db_overflow", "redis_in_use", "redis_available". Useful for
    # monitoring connection-pool saturation without a separate /metrics scrape.
    pool_stats: dict[str, int] = Field(default_factory=dict)
    timestamp: datetime


__all__ = [
    "AcceptedResponse",
    "ErrorPayloadSchema",
    "ErrorResponse",
    "HealthResponse",
]
