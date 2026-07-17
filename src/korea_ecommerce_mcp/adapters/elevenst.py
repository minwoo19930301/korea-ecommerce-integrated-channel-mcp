from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree

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


class ElevenStreetAdapter(ChannelAdapter):
    """Raw-XML adapter for the seller API.

    The public seller documentation does not expose a stable, unauthenticated
    specification for the create and full-update routes. Their defaults match
    the currently documented seller service convention, but they remain
    explicitly experimental and can be replaced per request with
    ``ChannelPayload.metadata``.
    """

    channel = ChannelId.ELEVENST

    DEFAULT_CREATE_PATH = "/prodservices/product"
    DEFAULT_UPDATE_PATH = "/prodservices/product/{external_id}"
    DEFAULT_DELETE_PATH = "/prodservices/product/{external_id}"
    DEFAULT_STOP_PATH = "/prodstatservice/stat/stopdisplay/{external_id}"
    DEFAULT_RESUME_PATH = "/prodstatservice/stat/restartdisplay/{external_id}"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(client or httpx.AsyncClient(timeout=settings.request_timeout_seconds))
        self._owns_client = client is None
        self.settings = settings
        self.api_key = settings.elevenst_api_key
        self.base_url = settings.elevenst_base_url.rstrip("/")
        self.create_path = settings.elevenst_create_path
        self.update_path = settings.elevenst_update_path
        self.delete_path = settings.elevenst_delete_path
        self.stop_path = settings.elevenst_stop_path
        self.resume_path = settings.elevenst_resume_path

    @property
    def configured(self) -> bool:
        return bool(
            self.api_key and self.base_url and urlsplit(self.base_url).scheme.casefold() == "https"
        )

    def capability(self) -> ChannelCapability:
        return ChannelCapability(
            channel=self.channel,
            configured=self.configured,
            notes=[
                "create and full update are experimental until verified against "
                "the seller account contract",
                "create_path/update_path metadata can override the experimental route templates",
                "requests require a complete channel-native XML document",
            ],
        )

    async def health(self) -> dict[str, Any]:
        # There is no side-effect-free, credential-neutral health endpoint.
        return {
            "channel": self.channel.value,
            "configured": self.configured,
            "ok": None,
            "network_checked": False,
            "status": "configuration_only" if self.configured else "invalid_configuration",
            "base_url": self.base_url,
            "experimental": {"create": True, "update": True},
        }

    async def create(
        self, product: MasterProduct, payload: ChannelPayload, idempotency_key: str
    ) -> AdapterResult:
        del product, idempotency_key
        operation = ProductOperation.CREATE
        try:
            body = self._require_xml(payload)
            path = self._payload_path(payload, "create_path", self.create_path)
            response = await self._request(
                "POST", path, body=body, content_type=payload.content_type
            )
            return self._response_result(response, operation, experimental=True)
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
            body = self._require_xml(payload)
            template = self._payload_path(payload, "update_path", self.update_path)
            path = self._render_path(template, external_id)
            response = await self._request(
                "PUT", path, body=body, content_type=payload.content_type
            )
            return self._response_result(
                response,
                operation,
                external_id=external_id,
                experimental=True,
            )
        except Exception as exc:
            return self.result_error(channel=self.channel, operation=operation.value, error=exc)

    async def set_enabled(
        self, external_id: str, enabled: bool, idempotency_key: str
    ) -> AdapterResult:
        del idempotency_key
        operation = ProductOperation.RESUME if enabled else ProductOperation.STOP
        try:
            template = self.resume_path if enabled else self.stop_path
            response = await self._request("PUT", self._render_path(template, external_id))
            return self._response_result(response, operation, external_id=external_id)
        except Exception as exc:
            return self.result_error(channel=self.channel, operation=operation.value, error=exc)

    async def delete(self, external_id: str, idempotency_key: str) -> AdapterResult:
        del idempotency_key
        operation = ProductOperation.DELETE
        try:
            response = await self._request(
                "DELETE", self._render_path(self.delete_path, external_id)
            )
            return self._response_result(response, operation, external_id=external_id)
        except Exception as exc:
            return self.result_error(channel=self.channel, operation=operation.value, error=exc)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: str | None = None,
        content_type: str | None = None,
    ) -> httpx.Response:
        self._require_configured()
        headers = {
            "openapikey": self.api_key or "",
            "Accept": "application/xml",
        }
        if body is not None:
            headers["Content-Type"] = content_type or "text/xml; charset=UTF-8"
        return await self.client.request(
            method,
            self._url(path),
            headers=headers,
            content=body,
        )

    def _require_configured(self) -> None:
        if not self.configured:
            raise AdapterConfigurationError(
                "elevenst_api_key and an HTTPS elevenst_base_url are required"
            )

    @staticmethod
    def _require_xml(payload: ChannelPayload) -> str:
        if not isinstance(payload.body, str) or not payload.body.strip():
            raise ValueError("elevenst payload.body must be a non-empty raw XML string")
        try:
            ElementTree.fromstring(payload.body)
        except ElementTree.ParseError as exc:
            raise ValueError(f"elevenst payload.body is not well-formed XML: {exc}") from exc
        return payload.body

    @staticmethod
    def _payload_path(payload: ChannelPayload, metadata_key: str, default: str) -> str:
        candidate = payload.metadata.get(metadata_key)
        if candidate is None:
            candidate = payload.metadata.get("endpoint_path")
        if candidate is None:
            candidate = default
        if not isinstance(candidate, str) or not candidate.strip():
            raise ValueError(f"{metadata_key} must be a non-empty relative path")
        return candidate.strip()

    @staticmethod
    def _render_path(template: str, external_id: str) -> str:
        encoded = quote(external_id, safe="")
        if "{external_id}" in template:
            return template.replace("{external_id}", encoded)
        return f"{template.rstrip('/')}/{encoded}"

    def _url(self, path: str) -> str:
        if "://" in path or not path.startswith("/"):
            raise ValueError("endpoint override must be an absolute path on the configured host")
        return f"{self.base_url}{path}"

    def _response_result(
        self,
        response: httpx.Response,
        operation: ProductOperation,
        *,
        external_id: str | None = None,
        experimental: bool = False,
    ) -> AdapterResult:
        if not response.is_success:
            return self.result_error(
                channel=self.channel,
                operation=operation.value,
                response=response,
                error=f"HTTP {response.status_code}",
            )

        data = self._parse_xml(response.text)
        code = self._find_value(data, {"resultcode", "code"})
        if code is not None and str(code).strip().lower() not in {
            "0",
            "200",
            "ok",
            "success",
        }:
            message = self._find_value(data, {"resultmessage", "message", "errormessage"})
            return AdapterResult(
                channel=self.channel,
                operation=operation,
                success=False,
                external_id=external_id,
                status_code=response.status_code,
                error=str(message or f"provider result code: {code}"),
                data={"response": data, "experimental_endpoint": experimental},
            )

        resolved_id = external_id or self._find_value(data, {"prdno", "productno", "productcode"})
        if operation is ProductOperation.CREATE and resolved_id is None:
            return AdapterResult(
                channel=self.channel,
                operation=operation,
                success=False,
                status_code=response.status_code,
                error="create response did not contain a product number; reconcile before retry",
                data={
                    "response": data,
                    "experimental_endpoint": experimental,
                    "outcome_uncertain": True,
                },
            )
        return AdapterResult(
            channel=self.channel,
            operation=operation,
            success=True,
            external_id=str(resolved_id) if resolved_id is not None else None,
            status_code=response.status_code,
            data={"response": data, "experimental_endpoint": experimental},
        )

    @classmethod
    def _parse_xml(cls, text: str) -> dict[str, Any]:
        if not text.strip():
            return {}
        try:
            root = ElementTree.fromstring(text)
        except ElementTree.ParseError:
            return {"raw": text}
        return {cls._local_name(root.tag): cls._element_value(root)}

    @classmethod
    def _element_value(cls, element: ElementTree.Element) -> Any:
        children = list(element)
        if not children:
            return (element.text or "").strip()
        result: dict[str, Any] = {}
        for child in children:
            key = cls._local_name(child.tag)
            value = cls._element_value(child)
            if key in result:
                existing = result[key]
                if not isinstance(existing, list):
                    result[key] = [existing]
                result[key].append(value)
            else:
                result[key] = value
        return result

    @staticmethod
    def _local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    @classmethod
    def _find_value(cls, value: Any, names: set[str]) -> Any | None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if key.lower() in names and not isinstance(nested, (dict, list)):
                    return nested
            for nested in value.values():
                found = cls._find_value(nested, names)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for nested in value:
                found = cls._find_value(nested, names)
                if found is not None:
                    return found
        return None
