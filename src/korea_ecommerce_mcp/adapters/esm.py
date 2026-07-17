from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

import httpx
import jwt

from korea_ecommerce_mcp.adapters.base import AdapterConfigurationError, ChannelAdapter
from korea_ecommerce_mcp.config import Settings
from korea_ecommerce_mcp.models import (
    AdapterResult,
    ChannelCapability,
    ChannelId,
    ChannelPayload,
    MasterProduct,
    ProductOperation,
)


class EsmAdapter(ChannelAdapter):
    """Adapter for the official item/v1 goods API."""

    channel = ChannelId.ESM

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(client or httpx.AsyncClient(timeout=settings.request_timeout_seconds))
        self._owns_client = client is None
        self.settings = settings
        self.base_url = settings.esm_base_url.rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(
            self.settings.esm_master_id
            and self.settings.esm_secret_key
            and self.settings.esm_issuer
            and (self.settings.esm_gmarket_seller_id or self.settings.esm_auction_seller_id)
        )

    def capability(self) -> ChannelCapability:
        return ChannelCapability(
            channel=self.channel,
            configured=self.configured,
            notes=[
                "uses the official item/v1 goods and sell-status endpoints",
                "hard delete requires every linked site product to be stopped first",
                "sell-status preserves the current price, stock, and selling period",
            ],
        )

    async def health(self) -> dict[str, Any]:
        # Avoid spending rate limit or requiring a real product identifier.
        return {
            "channel": self.channel.value,
            "configured": self.configured,
            "ok": None,
            "network_checked": False,
            "status": "configuration_only" if self.configured else "invalid_configuration",
            "base_url": self.base_url,
            "sites": {
                "gmarket": bool(self.settings.esm_gmarket_seller_id),
                "auction": bool(self.settings.esm_auction_seller_id),
            },
        }

    async def create(
        self, product: MasterProduct, payload: ChannelPayload, idempotency_key: str
    ) -> AdapterResult:
        del product, idempotency_key
        operation = ProductOperation.CREATE
        try:
            body = self.require_mapping(payload)
            response = await self._request("POST", "/goods", body=body)
            return self._response_result(response, operation)
        except Exception as exc:
            return self.result_error(channel=self.channel, operation=operation.value, error=exc)

    async def update(
        self,
        external_id: str,
        product: MasterProduct,
        payload: ChannelPayload,
        idempotency_key: str,
    ) -> AdapterResult:
        del product, idempotency_key
        operation = ProductOperation.UPDATE
        try:
            body = self.require_mapping(payload)
            response = await self._request(
                "PUT", f"/goods/{quote(external_id, safe='')}", body=body
            )
            return self._response_result(response, operation, external_id=external_id)
        except Exception as exc:
            return self.result_error(channel=self.channel, operation=operation.value, error=exc)

    async def set_enabled(
        self, external_id: str, enabled: bool, idempotency_key: str
    ) -> AdapterResult:
        del idempotency_key
        operation = ProductOperation.RESUME if enabled else ProductOperation.STOP
        path = f"/goods/{quote(external_id, safe='')}/sell-status"
        try:
            current_response = await self._request("GET", path)
            current_error = self._response_error(
                current_response, operation, external_id=external_id
            )
            if current_error is not None:
                return current_error
            current = self._json_object(current_response)
            body = self._sell_status_body(current, enabled)
            response = await self._request("PUT", path, body=body)
            return self._response_result(
                response,
                operation,
                external_id=external_id,
                extra_data={"sell_status_request": body},
            )
        except Exception as exc:
            return self.result_error(channel=self.channel, operation=operation.value, error=exc)

    async def delete(self, external_id: str, idempotency_key: str) -> AdapterResult:
        del idempotency_key
        operation = ProductOperation.DELETE
        try:
            response = await self._request("DELETE", f"/goods/{quote(external_id, safe='')}")
            return self._response_result(response, operation, external_id=external_id)
        except Exception as exc:
            return self.result_error(channel=self.channel, operation=operation.value, error=exc)

    async def _request(
        self, method: str, path: str, *, body: dict[str, Any] | None = None
    ) -> httpx.Response:
        self._require_configured()
        return await self.client.request(
            method,
            f"{self.base_url}{path}",
            headers={
                "Authorization": f"Bearer {self._token()}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json=body,
        )

    def _require_configured(self) -> None:
        if not self.configured:
            raise AdapterConfigurationError(
                "esm_master_id, esm_secret_key, esm_issuer, and at least one seller id are required"
            )

    def _token(self) -> str:
        master_id = self.settings.esm_master_id
        secret_key = self.settings.esm_secret_key
        issuer = self.settings.esm_issuer
        if not master_id or not secret_key or not issuer:
            raise AdapterConfigurationError("ESM JWT credentials are incomplete")
        payload = {
            "iss": issuer,
            "sub": "sell",
            "aud": "sa.esmplus.com",
            "iat": int(time.time()),
            "ssi": self._seller_identity(),
        }
        return jwt.encode(
            payload,
            secret_key,
            algorithm="HS256",
            headers={"kid": master_id, "typ": "JWT"},
        )

    def _seller_identity(self) -> str:
        identities: list[str] = []
        if self.settings.esm_auction_seller_id:
            identities.append(f"A:{self.settings.esm_auction_seller_id}")
        if self.settings.esm_gmarket_seller_id:
            identities.append(f"G:{self.settings.esm_gmarket_seller_id}")
        if not identities:
            raise AdapterConfigurationError("at least one ESM seller id is required")
        return ",".join(identities)

    def _sell_status_body(self, current: dict[str, Any], enabled: bool) -> dict[str, Any]:
        item_info = self._case_get(current, "itemBasicInfo")
        if not isinstance(item_info, dict):
            raise ValueError("sell-status response is missing itemBasicInfo")

        fields: dict[str, dict[str, Any]] = {}
        for name in ("price", "stock", "sellingPeriod"):
            value = self._case_get(item_info, name)
            if not isinstance(value, dict):
                raise ValueError(f"sell-status response is missing itemBasicInfo.{name}")
            fields[name] = value.copy()

        current_is_sell = self._case_get(current, "isSell")
        if not isinstance(current_is_sell, dict):
            current_is_sell = {}
        gmkt_current = bool(self._case_get(current_is_sell, "gmkt", False))
        iac_current = bool(self._case_get(current_is_sell, "iac", False))
        is_sell = {
            "gmkt": enabled if self.settings.esm_gmarket_seller_id else gmkt_current,
            "iac": enabled if self.settings.esm_auction_seller_id else iac_current,
        }
        return {"isSell": is_sell, "itemBasicInfo": fields}

    def _response_result(
        self,
        response: httpx.Response,
        operation: ProductOperation,
        *,
        external_id: str | None = None,
        extra_data: dict[str, Any] | None = None,
    ) -> AdapterResult:
        error = self._response_error(response, operation, external_id=external_id)
        if error is not None:
            return error
        data = self._json_object(response)
        resolved_id = external_id or self._case_get(data, "goodsNo")
        if operation is ProductOperation.CREATE and resolved_id is None:
            return AdapterResult(
                channel=self.channel,
                operation=operation,
                success=False,
                status_code=response.status_code,
                error="create response did not contain goodsNo; reconcile before retry",
                data={"response": data, "outcome_uncertain": True},
            )
        result_data = {"response": data}
        if extra_data:
            result_data.update(extra_data)
        return AdapterResult(
            channel=self.channel,
            operation=operation,
            success=True,
            external_id=str(resolved_id) if resolved_id is not None else None,
            status_code=response.status_code,
            data=result_data,
        )

    def _response_error(
        self,
        response: httpx.Response,
        operation: ProductOperation,
        *,
        external_id: str | None = None,
    ) -> AdapterResult | None:
        if not response.is_success:
            return self.result_error(
                channel=self.channel,
                operation=operation.value,
                response=response,
                error=f"HTTP {response.status_code}",
            )
        data = self._json_object(response)
        result_code = self._case_get(data, "resultCode")
        if result_code is not None and str(result_code) not in {"0", "200"}:
            message = self._case_get(data, "message")
            return AdapterResult(
                channel=self.channel,
                operation=operation,
                success=False,
                external_id=external_id,
                status_code=response.status_code,
                error=str(message or f"provider result code: {result_code}"),
                data={"response": data},
            )
        return None

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, Any]:
        if not response.content:
            return {}
        try:
            value = response.json()
        except ValueError:
            return {"raw": response.text}
        return value if isinstance(value, dict) else {"value": value}

    @staticmethod
    def _case_get(mapping: dict[str, Any], key: str, default: Any = None) -> Any:
        expected = key.casefold()
        for candidate, value in mapping.items():
            if candidate.casefold() == expected:
                return value
        return default
