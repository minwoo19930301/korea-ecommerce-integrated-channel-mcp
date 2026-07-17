from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, PositiveInt, field_validator


class ChannelId(StrEnum):
    SMARTSTORE = "smartstore"
    COUPANG = "coupang"
    ELEVENST = "elevenst"
    ESM = "esm"


class ProductOperation(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    STOP = "stop"
    RESUME = "resume"
    DELETE = "delete"


class MasterProduct(BaseModel):
    """Channel-neutral product data persisted as the source of truth."""

    sku: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=500)
    price: PositiveInt
    stock: int = Field(ge=0, le=99_999_999)
    description_html: str = ""
    image_urls: list[str] = Field(default_factory=list, max_length=20)
    brand: str | None = None
    manufacturer: str | None = None
    model_name: str | None = None
    barcode: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("sku", "name")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class ChannelPayload(BaseModel):
    """Exact channel request body after template rendering or manual override."""

    body: dict[str, Any] | str = Field(default_factory=dict)
    content_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProductCommand(BaseModel):
    product: MasterProduct
    channels: list[ChannelId] = Field(min_length=1)
    profile_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]+$")
    payloads: dict[ChannelId, ChannelPayload] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=8, max_length=200)
    dry_run: bool = True
    confirm: Literal["PREVIEW", "EXECUTE"] = "PREVIEW"
    approval_token: str | None = Field(default=None, min_length=20, max_length=300)

    @field_validator("channels")
    @classmethod
    def unique_channels(cls, channels: list[ChannelId]) -> list[ChannelId]:
        if len(set(channels)) != len(channels):
            raise ValueError("channels must be unique")
        return channels


class UpdateCommand(ProductCommand):
    expected_external_ids: dict[ChannelId, str] = Field(
        default_factory=dict,
        description="Optional safety assertions; values must match stored SKU mappings.",
    )


class StatusCommand(BaseModel):
    sku: str = Field(min_length=1, max_length=100)
    channels: list[ChannelId] = Field(min_length=1)
    expected_external_ids: dict[ChannelId, str] = Field(
        default_factory=dict,
        description="Optional safety assertions; values must match stored SKU mappings.",
    )
    idempotency_key: str = Field(min_length=8, max_length=200)
    dry_run: bool = True
    confirm: Literal["PREVIEW", "EXECUTE"] = "PREVIEW"
    approval_token: str | None = Field(default=None, min_length=20, max_length=300)


class DeleteCommand(StatusCommand):
    confirm_delete: Literal["NO", "DELETE"] = "NO"


class AdapterResult(BaseModel):
    channel: ChannelId
    operation: ProductOperation
    success: bool
    external_id: str | None = None
    status_code: int | None = None
    retryable: bool = False
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    preview: dict[str, Any] = Field(default_factory=dict)


class FanoutResult(BaseModel):
    job_id: str
    sku: str
    operation: ProductOperation
    dry_run: bool
    request_sha256: str = ""
    approval_token: str | None = None
    approval_expires_at: datetime | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    results: list[AdapterResult] = Field(default_factory=list)

    @property
    def all_succeeded(self) -> bool:
        return bool(self.results) and all(item.success for item in self.results)


class ChannelCapability(BaseModel):
    channel: ChannelId
    configured: bool
    create: bool = True
    update: bool = True
    stop: bool = True
    resume: bool = True
    delete: bool = True
    notes: list[str] = Field(default_factory=list)
