from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from typing import Any

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

_PRODUCT_PATH = "/v2/providers/seller_api/apis/api/v1/marketplace/seller-products"
_VENDOR_ITEM_PATH = "/v2/providers/seller_api/apis/api/v1/marketplace/vendor-items"


class CoupangAdapter(ChannelAdapter):
    """Adapter for seller-product CRUD and vendor-item sale status APIs."""

    channel = ChannelId.COUPANG

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(client or httpx.AsyncClient(timeout=settings.request_timeout_seconds))
        self._owns_client = client is None
        self.settings = settings
        self.base_url = settings.coupang_base_url.rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(
            self.settings.coupang_vendor_id
            and self.settings.coupang_access_key
            and self.settings.coupang_secret_key
        )

    def capability(self) -> ChannelCapability:
        return ChannelCapability(
            channel=self.channel,
            configured=self.configured,
            notes=[
                "All adapter operations accept sellerProductId values.",
                "Stop and resume look up the product and update every vendor item.",
                "Every option must be stopped before an approved product can be deleted.",
                "Product payloads must follow the official seller-product schema.",
            ],
        )

    def _require_configured(self) -> tuple[str, str, str]:
        missing: list[str] = []
        if not self.settings.coupang_vendor_id:
            missing.append("coupang_vendor_id")
        if not self.settings.coupang_access_key:
            missing.append("coupang_access_key")
        if not self.settings.coupang_secret_key:
            missing.append("coupang_secret_key")
        if missing:
            raise AdapterConfigurationError("missing Coupang credentials: " + ", ".join(missing))
        return (
            self.settings.coupang_vendor_id,
            self.settings.coupang_access_key,
            self.settings.coupang_secret_key,
        )

    @staticmethod
    def generate_authorization(
        method: str,
        path: str,
        query: str,
        access_key: str,
        secret_key: str,
        signed_date: str,
    ) -> str:
        message = f"{signed_date}{method.upper()}{path}{query}"
        signature = hmac.new(secret_key.encode(), message.encode(), hashlib.sha256).hexdigest()
        return (
            "CEA algorithm=HmacSHA256, "
            f"access-key={access_key}, signed-date={signed_date}, signature={signature}"
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        vendor_id, access_key, secret_key = self._require_configured()
        query = str(httpx.QueryParams(params)) if params else ""
        signed_date = datetime.now(UTC).strftime("%y%m%dT%H%M%SZ")
        headers = {
            "Authorization": self.generate_authorization(
                method, path, query, access_key, secret_key, signed_date
            ),
            "X-Requested-By": vendor_id,
            "X-MARKET": "KR",
            "Accept": "application/json",
        }
        if json is not None:
            headers["Content-Type"] = "application/json"
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"
        return await self.client.request(method, url, headers=headers, json=json)

    @staticmethod
    def _response_data(response: httpx.Response) -> dict[str, Any]:
        if not response.content:
            return {}
        data = response.json()
        return data if isinstance(data, dict) else {"response": data}

    @classmethod
    def _api_error(cls, data: Any) -> str | None:
        if isinstance(data, dict):
            code = str(data.get("code", "")).upper()
            if code in {"ERROR", "FAIL", "FAILED", "FAILURE"}:
                return str(data.get("message") or f"API returned {code}")
            for value in data.values():
                error = cls._api_error(value)
                if error:
                    return error
        elif isinstance(data, list):
            for value in data:
                error = cls._api_error(value)
                if error:
                    return error
        return None

    @staticmethod
    def _created_product_id(data: dict[str, Any]) -> str | None:
        def find_named(value: Any) -> Any:
            if isinstance(value, dict):
                if value.get("sellerProductId") is not None:
                    return value["sellerProductId"]
                for child in value.values():
                    found = find_named(child)
                    if found is not None:
                        return found
            elif isinstance(value, list):
                for child in value:
                    found = find_named(child)
                    if found is not None:
                        return found
            return None

        product_id = find_named(data)
        if product_id is not None:
            return str(product_id)

        value: Any = data
        while isinstance(value, dict) and "data" in value:
            value = value["data"]
        return str(value) if isinstance(value, (int, str)) and str(value) else None

    @staticmethod
    def _vendor_item_ids(data: dict[str, Any]) -> list[str]:
        found: list[str] = []

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                items = value.get("items")
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict) and item.get("vendorItemId") is not None:
                            item_id = str(item["vendorItemId"])
                            if item_id not in found:
                                found.append(item_id)
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(data)
        return found

    @staticmethod
    def _numeric_id(external_id: str, name: str) -> str:
        value = external_id.strip()
        if not value or not value.isascii() or not value.isdecimal():
            raise ValueError(f"{name} must contain ASCII digits only")
        return value

    @staticmethod
    def _json_id(external_id: str) -> int:
        return int(external_id)

    def _product_body(
        self, payload: ChannelPayload, *, seller_product_id: str | None = None
    ) -> dict[str, Any]:
        vendor_id, _, _ = self._require_configured()
        body = dict(self.require_mapping(payload))
        provided_vendor_id = body.get("vendorId")
        if provided_vendor_id is not None and str(provided_vendor_id) != vendor_id:
            raise ValueError("payload vendorId does not match configured vendor")
        body["vendorId"] = vendor_id

        if seller_product_id is not None:
            provided_product_id = body.get("sellerProductId")
            if provided_product_id is not None and str(provided_product_id) != seller_product_id:
                raise ValueError("payload sellerProductId does not match requested seller product")
            body["sellerProductId"] = self._json_id(seller_product_id)
        return body

    def _error(
        self,
        operation: ProductOperation,
        error: Exception | str,
        response: httpx.Response | None = None,
    ) -> AdapterResult:
        if response is None and isinstance(error, httpx.HTTPStatusError):
            response = error.response
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
            response = await self._request(
                "GET",
                _PRODUCT_PATH,
                params={"vendorId": self.settings.coupang_vendor_id, "maxPerPage": 1},
            )
            response.raise_for_status()
            data = self._response_data(response)
            api_error = self._api_error(data)
            if api_error:
                return {
                    "channel": self.channel.value,
                    "configured": True,
                    "ok": False,
                    "status_code": response.status_code,
                    "error": api_error,
                }
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
        return {
            "channel": self.channel.value,
            "configured": True,
            "ok": True,
            "status_code": response.status_code,
        }

    async def create(
        self, product: MasterProduct, payload: ChannelPayload, idempotency_key: str
    ) -> AdapterResult:
        del product, idempotency_key
        operation = ProductOperation.CREATE
        try:
            body = self._product_body(payload)
            response = await self._request("POST", _PRODUCT_PATH, json=body)
            response.raise_for_status()
            data = self._response_data(response)
            if api_error := self._api_error(data):
                return self._error(operation, api_error, response)
            product_id = self._created_product_id(data)
            if product_id is None:
                return AdapterResult(
                    channel=self.channel,
                    operation=operation,
                    success=False,
                    status_code=response.status_code,
                    error="create response did not contain sellerProductId; reconcile before retry",
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
            product_id = self._numeric_id(external_id, "sellerProductId")
            body = self._product_body(payload, seller_product_id=product_id)
            response = await self._request("PUT", _PRODUCT_PATH, json=body)
            response.raise_for_status()
            data = self._response_data(response)
            if api_error := self._api_error(data):
                return self._error(operation, api_error, response)
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
            product_id = self._numeric_id(external_id, "sellerProductId")
            lookup_response = await self._request("GET", f"{_PRODUCT_PATH}/{product_id}")
            lookup_response.raise_for_status()
            lookup_data = self._response_data(lookup_response)
            if api_error := self._api_error(lookup_data):
                return self._error(operation, api_error, lookup_response)

            vendor_item_ids = self._vendor_item_ids(lookup_data)
            if not vendor_item_ids:
                return AdapterResult(
                    channel=self.channel,
                    operation=operation,
                    success=False,
                    external_id=product_id,
                    status_code=lookup_response.status_code,
                    error="product response did not contain any vendorItemId values",
                    data={"vendor_item_results": []},
                )

            action = "resume" if enabled else "stop"
            item_results: list[dict[str, Any]] = []
            first_failure_status: int | None = None
            retryable = False
            for vendor_item_id in vendor_item_ids:
                try:
                    response = await self._request(
                        "PUT", f"{_VENDOR_ITEM_PATH}/{vendor_item_id}/sales/{action}"
                    )
                    data = self._response_data(response)
                    api_error = self._api_error(data)
                    succeeded = response.is_success and api_error is None
                    item_result: dict[str, Any] = {
                        "vendor_item_id": vendor_item_id,
                        "success": succeeded,
                        "status_code": response.status_code,
                        "data": data,
                    }
                    if not succeeded:
                        item_result["error"] = api_error or response.text[:2000]
                        if first_failure_status is None:
                            first_failure_status = response.status_code
                        retryable = retryable or response.status_code in {
                            408,
                            425,
                            429,
                            500,
                            502,
                            503,
                            504,
                        }
                    item_results.append(item_result)
                except Exception as error:
                    status = (
                        error.response.status_code
                        if isinstance(error, httpx.HTTPStatusError)
                        else None
                    )
                    if first_failure_status is None:
                        first_failure_status = status
                    retryable = retryable or isinstance(error, httpx.TransportError)
                    item_results.append(
                        {
                            "vendor_item_id": vendor_item_id,
                            "success": False,
                            "status_code": status,
                            "error": str(error),
                        }
                    )

            all_succeeded = all(item["success"] for item in item_results)
            return AdapterResult(
                channel=self.channel,
                operation=operation,
                success=all_succeeded,
                external_id=product_id,
                status_code=(
                    lookup_response.status_code if all_succeeded else first_failure_status
                ),
                retryable=retryable,
                error=(None if all_succeeded else "one or more vendor-item status updates failed"),
                data={"vendor_item_results": item_results},
            )
        except Exception as error:
            return self._error(operation, error)

    async def delete(self, external_id: str, idempotency_key: str) -> AdapterResult:
        del idempotency_key
        operation = ProductOperation.DELETE
        try:
            product_id = self._numeric_id(external_id, "sellerProductId")
            response = await self._request("DELETE", f"{_PRODUCT_PATH}/{product_id}")
            response.raise_for_status()
            data = self._response_data(response)
            if api_error := self._api_error(data):
                return self._error(operation, api_error, response)
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
