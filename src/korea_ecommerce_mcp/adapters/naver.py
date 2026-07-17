from __future__ import annotations

import asyncio
import base64
import time
from typing import Any

import bcrypt
import httpx

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


class NaverAdapter(ChannelAdapter):
    """Adapter for the Commerce API product endpoints.

    Payloads are deliberately passed through as channel-native JSON. Product
    registration requires category, notice, delivery, and other data that
    cannot be inferred safely from the channel-neutral product model.
    """

    channel = ChannelId.SMARTSTORE

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(client or httpx.AsyncClient(timeout=settings.request_timeout_seconds))
        self._owns_client = client is None
        self.settings = settings
        self.base_url = settings.naver_base_url.rstrip("/")
        self._access_token: str | None = None
        self._access_token_valid_until = 0.0
        self._token_lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.settings.naver_client_id and self.settings.naver_client_secret)

    def capability(self) -> ChannelCapability:
        token_type = "SELLER" if self.settings.naver_account_id else "SELF"
        return ChannelCapability(
            channel=self.channel,
            configured=self.configured,
            notes=[
                "Product identifiers are originProductNo values.",
                f"OAuth token type: {token_type}.",
                "Product payloads must follow the official v2 schema.",
            ],
        )

    def _require_configured(self) -> tuple[str, str]:
        missing: list[str] = []
        if not self.settings.naver_client_id:
            missing.append("naver_client_id")
        if not self.settings.naver_client_secret:
            missing.append("naver_client_secret")
        if missing:
            raise AdapterConfigurationError("missing Naver credentials: " + ", ".join(missing))
        return self.settings.naver_client_id, self.settings.naver_client_secret

    @staticmethod
    def generate_client_secret_sign(client_id: str, client_secret: str, timestamp_ms: int) -> str:
        password = f"{client_id}_{timestamp_ms}".encode()
        hashed = bcrypt.hashpw(password, client_secret.encode())
        return base64.standard_b64encode(hashed).decode("ascii")

    async def _token(self, *, force_refresh: bool = False) -> str:
        client_id, client_secret = self._require_configured()
        now = time.monotonic()
        if not force_refresh and self._access_token and now < self._access_token_valid_until:
            return self._access_token

        async with self._token_lock:
            now = time.monotonic()
            if not force_refresh and self._access_token and now < self._access_token_valid_until:
                return self._access_token

            timestamp_ms = int(time.time() * 1000)
            form: dict[str, str] = {
                "client_id": client_id,
                "timestamp": str(timestamp_ms),
                "client_secret_sign": self.generate_client_secret_sign(
                    client_id, client_secret, timestamp_ms
                ),
                "grant_type": "client_credentials",
                "type": "SELLER" if self.settings.naver_account_id else "SELF",
            }
            if self.settings.naver_account_id:
                form["account_id"] = self.settings.naver_account_id

            response = await self.client.post(
                f"{self.base_url}/v1/oauth2/token",
                data=form,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or not data.get("access_token"):
                raise RuntimeError("token response did not contain access_token")

            self._access_token = str(data["access_token"])
            try:
                expires_in = float(data.get("expires_in", 10_800))
            except (TypeError, ValueError):
                expires_in = 10_800
            self._access_token_valid_until = time.monotonic() + max(0.0, expires_in - 60)
            return self._access_token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        token = await self._token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json;charset=UTF-8",
        }
        response = await self.client.request(
            method, f"{self.base_url}{path}", headers=headers, json=json
        )

        # The API explicitly recommends one token refresh for this gateway code.
        if response.status_code == 401 and self._response_code(response) == "GW.AUTHN":
            token = await self._token(force_refresh=True)
            headers["Authorization"] = f"Bearer {token}"
            response = await self.client.request(
                method, f"{self.base_url}{path}", headers=headers, json=json
            )
        return response

    @staticmethod
    def _response_code(response: httpx.Response) -> str | None:
        try:
            data = response.json()
        except ValueError:
            return None
        return str(data.get("code")) if isinstance(data, dict) and data.get("code") else None

    @staticmethod
    def _response_data(response: httpx.Response) -> dict[str, Any]:
        if not response.content:
            return {}
        data = response.json()
        return data if isinstance(data, dict) else {"response": data}

    @staticmethod
    def _origin_product_id(data: dict[str, Any]) -> str | None:
        value = data.get("originProductNo")
        if value is None and isinstance(data.get("data"), dict):
            value = data["data"].get("originProductNo")
        return str(value) if value is not None else None

    @staticmethod
    def _numeric_id(external_id: str, name: str) -> str:
        value = external_id.strip()
        if not value or not value.isascii() or not value.isdecimal():
            raise ValueError(f"{name} must contain ASCII digits only")
        return value

    def _error(self, operation: ProductOperation, error: Exception) -> AdapterResult:
        response = error.response if isinstance(error, httpx.HTTPStatusError) else None
        return self.result_error(
            channel=self.channel,
            operation=operation.value,
            response=response,
            error=error,
        )

    async def health(self) -> dict[str, Any]:
        if not self.configured:
            return {
                "channel": self.channel.value,
                "configured": False,
                "ok": False,
                "error": "credentials are not configured",
            }
        try:
            await self._token()
        except Exception as error:  # returned to MCP callers as health data
            status = (
                error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
            )
            return {
                "channel": self.channel.value,
                "configured": True,
                "ok": False,
                "status_code": status,
                "error": str(error),
            }
        return {"channel": self.channel.value, "configured": True, "ok": True}

    async def create(
        self, product: MasterProduct, payload: ChannelPayload, idempotency_key: str
    ) -> AdapterResult:
        del product, idempotency_key
        operation = ProductOperation.CREATE
        try:
            body = dict(self.require_mapping(payload))
            response = await self._request("POST", "/v2/products", json=body)
            response.raise_for_status()
            data = self._response_data(response)
            product_id = self._origin_product_id(data)
            if product_id is None:
                return AdapterResult(
                    channel=self.channel,
                    operation=operation,
                    success=False,
                    status_code=response.status_code,
                    error="create response did not contain originProductNo; reconcile before retry",
                    data={**data, "outcome_uncertain": True},
                )
            return AdapterResult(
                channel=self.channel,
                operation=operation,
                success=True,
                external_id=product_id,
                status_code=response.status_code,
                data=data,
            )
        except Exception as error:
            return self._error(operation, error)

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
            product_id = self._numeric_id(external_id, "originProductNo")
            body = dict(self.require_mapping(payload))
            response = await self._request(
                "PUT", f"/v2/products/origin-products/{product_id}", json=body
            )
            response.raise_for_status()
            data = self._response_data(response)
            return AdapterResult(
                channel=self.channel,
                operation=operation,
                success=True,
                external_id=product_id,
                status_code=response.status_code,
                data=data,
            )
        except Exception as error:
            return self._error(operation, error)

    async def set_enabled(
        self, external_id: str, enabled: bool, idempotency_key: str
    ) -> AdapterResult:
        del idempotency_key
        operation = ProductOperation.RESUME if enabled else ProductOperation.STOP
        try:
            product_id = self._numeric_id(external_id, "originProductNo")
            response = await self._request(
                "PUT",
                f"/v1/products/origin-products/{product_id}/change-status",
                json={"statusType": "SALE" if enabled else "SUSPENSION"},
            )
            response.raise_for_status()
            data = self._response_data(response)
            return AdapterResult(
                channel=self.channel,
                operation=operation,
                success=True,
                external_id=product_id,
                status_code=response.status_code,
                data=data,
            )
        except Exception as error:
            return self._error(operation, error)

    async def delete(self, external_id: str, idempotency_key: str) -> AdapterResult:
        del idempotency_key
        operation = ProductOperation.DELETE
        try:
            product_id = self._numeric_id(external_id, "originProductNo")
            response = await self._request("DELETE", f"/v2/products/origin-products/{product_id}")
            response.raise_for_status()
            data = self._response_data(response)
            return AdapterResult(
                channel=self.channel,
                operation=operation,
                success=True,
                external_id=product_id,
                status_code=response.status_code,
                data=data,
            )
        except Exception as error:
            return self._error(operation, error)
