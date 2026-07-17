from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import httpx

from korea_ecommerce_mcp.models import (
    AdapterResult,
    ChannelCapability,
    ChannelId,
    ChannelPayload,
    MasterProduct,
)


class AdapterConfigurationError(RuntimeError):
    pass


class ChannelAdapter(ABC):
    channel: ChannelId

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=30.0)

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    @abstractmethod
    def capability(self) -> ChannelCapability:
        raise NotImplementedError

    @abstractmethod
    async def health(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def create(
        self, product: MasterProduct, payload: ChannelPayload, idempotency_key: str
    ) -> AdapterResult:
        raise NotImplementedError

    @abstractmethod
    async def update(
        self,
        external_id: str,
        product: MasterProduct,
        payload: ChannelPayload,
        idempotency_key: str,
    ) -> AdapterResult:
        raise NotImplementedError

    @abstractmethod
    async def set_enabled(
        self, external_id: str, enabled: bool, idempotency_key: str
    ) -> AdapterResult:
        raise NotImplementedError

    @abstractmethod
    async def delete(self, external_id: str, idempotency_key: str) -> AdapterResult:
        raise NotImplementedError

    @staticmethod
    def require_mapping(payload: ChannelPayload) -> dict[str, Any]:
        if not isinstance(payload.body, dict) or not payload.body:
            raise ValueError("this channel requires a non-empty JSON object payload")
        return payload.body

    @staticmethod
    def result_error(
        *,
        channel: ChannelId,
        operation: str,
        response: httpx.Response | None = None,
        error: Exception | str,
    ) -> AdapterResult:
        from korea_ecommerce_mcp.models import ProductOperation

        status = response.status_code if response is not None else None
        retryable = status in {408, 425, 429, 500, 502, 503, 504} if status else False
        return AdapterResult(
            channel=channel,
            operation=ProductOperation(operation),
            success=False,
            status_code=status,
            retryable=retryable,
            error=str(error),
            data={"response": response.text[:2000]} if response is not None else {},
        )
