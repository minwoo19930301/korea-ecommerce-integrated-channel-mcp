from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import uuid4

from korea_ecommerce_mcp.adapters.base import ChannelAdapter
from korea_ecommerce_mcp.config import Settings
from korea_ecommerce_mcp.models import (
    AdapterResult,
    ChannelId,
    ChannelPayload,
    DeleteCommand,
    FanoutResult,
    MasterProduct,
    ProductCommand,
    ProductOperation,
    StatusCommand,
    UpdateCommand,
)
from korea_ecommerce_mcp.store import SQLiteStore
from korea_ecommerce_mcp.templates import ProfileLoader


class MutationPolicyError(RuntimeError):
    pass


class MappingPolicyError(RuntimeError):
    pass


class IdempotencyConflictError(RuntimeError):
    pass


class IntegratedChannelService:
    """Coordinates one product operation across independent seller channels."""

    def __init__(
        self,
        *,
        settings: Settings,
        store: SQLiteStore,
        adapters: dict[ChannelId, ChannelAdapter],
        profiles: ProfileLoader | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.adapters = adapters
        self.profiles = profiles or ProfileLoader(settings.profile_directory)
        self._sku_locks: dict[str, asyncio.Lock] = {}
        self._approval_secret = secrets.token_bytes(32)

    async def close(self) -> None:
        await asyncio.gather(*(adapter.close() for adapter in self.adapters.values()))

    def capabilities(self) -> list[dict]:
        return [adapter.capability().model_dump(mode="json") for adapter in self.adapters.values()]

    async def health(self) -> dict[str, dict]:
        async def check(channel: ChannelId, adapter: ChannelAdapter) -> tuple[str, dict]:
            try:
                return channel.value, await adapter.health()
            except Exception as exc:  # health must never take down the MCP server
                return channel.value, {"configured": False, "ok": False, "error": str(exc)}

        pairs = await asyncio.gather(
            *(check(channel, adapter) for channel, adapter in self.adapters.items())
        )
        return dict(pairs)

    async def publish(self, command: ProductCommand) -> FanoutResult:
        if command.dry_run:
            return await self._publish_once(command)
        lock = self._sku_lock(command.product.sku)
        async with lock:
            return await self._publish_once(command)

    async def _publish_once(self, command: ProductCommand) -> FanoutResult:
        payloads = self._resolved_payloads(command)
        request_sha256 = self._product_request_hash(ProductOperation.CREATE, command, payloads)
        approval_sha256 = self._approval_request_hash(
            request_sha256, command.product.sku, command.channels
        )
        cached = self._cached(
            ProductOperation.CREATE,
            command.product.sku,
            command.idempotency_key,
            command.dry_run,
            request_sha256,
        )
        if cached:
            return cached

        if command.dry_run:
            return self._preview_product_command(
                ProductOperation.CREATE,
                command,
                payloads,
                request_sha256,
                approval_sha256,
            )

        self._require_execution(
            command.dry_run,
            command.confirm,
            command.approval_token,
            approval_sha256,
        )
        self.store.save_product(command.product)

        async def run(channel: ChannelId) -> AdapterResult:
            payload = payloads.get(channel)
            if payload is None:
                return self._missing_payload(channel, ProductOperation.CREATE)
            adapter = self.adapters.get(channel)
            if adapter is None:
                return self._missing_adapter(channel, ProductOperation.CREATE)
            try:
                existing = self.store.get_mapping(command.product.sku, channel)
                if existing is not None:
                    return AdapterResult(
                        channel=channel,
                        operation=ProductOperation.CREATE,
                        success=True,
                        external_id=existing["external_id"],
                        data={
                            "skipped": True,
                            "reason": "stored SKU mapping already exists; use update",
                        },
                    )
                return await adapter.create(command.product, payload, command.idempotency_key)
            except Exception as exc:
                return self._unexpected(channel, ProductOperation.CREATE, exc)

        result = await self._fanout(
            sku=command.product.sku,
            operation=ProductOperation.CREATE,
            channels=command.channels,
            runner=run,
            request_sha256=request_sha256,
        )
        for item in result.results:
            if item.success and item.external_id:
                self.store.upsert_mapping(
                    command.product.sku, item.channel, item.external_id, "active"
                )
        self._persist_execution(command.idempotency_key, result)
        return result

    async def update(self, command: UpdateCommand) -> FanoutResult:
        if command.dry_run:
            return await self._update_once(command)
        lock = self._sku_lock(command.product.sku)
        async with lock:
            return await self._update_once(command)

    async def _update_once(self, command: UpdateCommand) -> FanoutResult:
        payloads = self._resolved_payloads(command)
        request_sha256 = self._product_request_hash(ProductOperation.UPDATE, command, payloads)
        approval_sha256 = self._approval_request_hash(
            request_sha256, command.product.sku, command.channels
        )
        cached = self._cached(
            ProductOperation.UPDATE,
            command.product.sku,
            command.idempotency_key,
            command.dry_run,
            request_sha256,
        )
        if cached:
            return cached
        if command.dry_run:
            return self._preview_product_command(
                ProductOperation.UPDATE,
                command,
                payloads,
                request_sha256,
                approval_sha256,
            )

        self._require_execution(
            command.dry_run,
            command.confirm,
            command.approval_token,
            approval_sha256,
        )
        self.store.save_product(command.product)

        async def run(channel: ChannelId) -> AdapterResult:
            payload = payloads.get(channel)
            if payload is None:
                return self._missing_payload(channel, ProductOperation.UPDATE)
            adapter = self.adapters.get(channel)
            if adapter is None:
                return self._missing_adapter(channel, ProductOperation.UPDATE)
            try:
                external_id = self._resolve_external_id(
                    command.product.sku, channel, command.expected_external_ids
                )
                if not external_id:
                    return self._missing_external_id(channel, ProductOperation.UPDATE)
                return await adapter.update(
                    external_id, command.product, payload, command.idempotency_key
                )
            except Exception as exc:
                return self._unexpected(channel, ProductOperation.UPDATE, exc)

        result = await self._fanout(
            sku=command.product.sku,
            operation=ProductOperation.UPDATE,
            channels=command.channels,
            runner=run,
            request_sha256=request_sha256,
        )
        self._persist_execution(command.idempotency_key, result)
        return result

    async def set_enabled(self, command: StatusCommand, *, enabled: bool) -> FanoutResult:
        if command.dry_run:
            return await self._set_enabled_once(command, enabled=enabled)
        lock = self._sku_lock(command.sku)
        async with lock:
            return await self._set_enabled_once(command, enabled=enabled)

    async def _set_enabled_once(self, command: StatusCommand, *, enabled: bool) -> FanoutResult:
        operation = ProductOperation.RESUME if enabled else ProductOperation.STOP
        request_sha256 = self._status_request_hash(operation, command)
        approval_sha256 = self._approval_request_hash(request_sha256, command.sku, command.channels)
        cached = self._cached(
            operation,
            command.sku,
            command.idempotency_key,
            command.dry_run,
            request_sha256,
        )
        if cached:
            return cached
        if command.dry_run:
            return self._preview_status_command(operation, command, request_sha256, approval_sha256)

        self._require_execution(
            command.dry_run,
            command.confirm,
            command.approval_token,
            approval_sha256,
        )

        async def run(channel: ChannelId) -> AdapterResult:
            adapter = self.adapters.get(channel)
            if adapter is None:
                return self._missing_adapter(channel, operation)
            try:
                external_id = self._resolve_external_id(
                    command.sku, channel, command.expected_external_ids
                )
                if not external_id:
                    return self._missing_external_id(channel, operation)
                return await adapter.set_enabled(external_id, enabled, command.idempotency_key)
            except Exception as exc:
                return self._unexpected(channel, operation, exc)

        result = await self._fanout(
            sku=command.sku,
            operation=operation,
            channels=command.channels,
            runner=run,
            request_sha256=request_sha256,
        )
        status = "active" if enabled else "stopped"
        for item in result.results:
            if item.success and item.external_id:
                self.store.upsert_mapping(command.sku, item.channel, item.external_id, status)
        self._persist_execution(command.idempotency_key, result)
        return result

    async def delete(self, command: DeleteCommand) -> FanoutResult:
        if command.dry_run:
            return await self._delete_once(command)
        lock = self._sku_lock(command.sku)
        async with lock:
            return await self._delete_once(command)

    async def _delete_once(self, command: DeleteCommand) -> FanoutResult:
        request_sha256 = self._status_request_hash(ProductOperation.DELETE, command)
        approval_sha256 = self._approval_request_hash(request_sha256, command.sku, command.channels)
        cached = self._cached(
            ProductOperation.DELETE,
            command.sku,
            command.idempotency_key,
            command.dry_run,
            request_sha256,
        )
        if cached:
            return cached
        if command.dry_run:
            return self._preview_status_command(
                ProductOperation.DELETE,
                command,
                request_sha256,
                approval_sha256,
            )
        self._require_execution(
            command.dry_run,
            command.confirm,
            command.approval_token,
            approval_sha256,
        )
        if command.confirm_delete != "DELETE":
            raise MutationPolicyError("hard delete requires confirm_delete='DELETE'")

        async def run(channel: ChannelId) -> AdapterResult:
            adapter = self.adapters.get(channel)
            if adapter is None:
                return self._missing_adapter(channel, ProductOperation.DELETE)
            try:
                external_id = self._resolve_external_id(
                    command.sku, channel, command.expected_external_ids
                )
                if not external_id:
                    return self._missing_external_id(channel, ProductOperation.DELETE)
                return await adapter.delete(external_id, command.idempotency_key)
            except Exception as exc:
                return self._unexpected(channel, ProductOperation.DELETE, exc)

        result = await self._fanout(
            sku=command.sku,
            operation=ProductOperation.DELETE,
            channels=command.channels,
            runner=run,
            request_sha256=request_sha256,
        )
        for item in result.results:
            if item.success and item.external_id:
                self.store.upsert_mapping(command.sku, item.channel, item.external_id, "deleted")
        self._persist_execution(command.idempotency_key, result)
        return result

    def get_product(self, sku: str) -> dict:
        product = self.store.get_product(sku)
        mappings = self.store.list_mappings(sku)
        return {
            "product": (
                product.model_dump(mode="json") if isinstance(product, MasterProduct) else product
            ),
            "mappings": mappings,
        }

    def get_job(self, job_id: str) -> dict | None:
        job = self.store.get_job(job_id)
        if isinstance(job, FanoutResult):
            return job.model_dump(mode="json")
        return job

    def list_profiles(self) -> list[dict[str, str]]:
        return self.profiles.list_profiles()

    def preview_profile(self, profile_id: str, product: MasterProduct) -> dict:
        rendered = self.profiles.render(profile_id, product)
        return {
            channel.value: self._payload_preview(product, payload)
            for channel, payload in rendered.items()
        }

    async def _fanout(
        self,
        *,
        sku: str,
        operation: ProductOperation,
        channels: list[ChannelId],
        runner: Callable[[ChannelId], Awaitable[AdapterResult]],
        request_sha256: str,
    ) -> FanoutResult:
        result = FanoutResult(
            job_id=uuid4().hex,
            sku=sku,
            operation=operation,
            dry_run=False,
            request_sha256=request_sha256,
        )
        result.results = list(await asyncio.gather(*(runner(channel) for channel in channels)))
        result.finished_at = datetime.now(UTC)
        return result

    def _preview_product_command(
        self,
        operation: ProductOperation,
        command: ProductCommand,
        payloads: dict[ChannelId, ChannelPayload],
        request_sha256: str,
        approval_sha256: str,
    ) -> FanoutResult:
        results: list[AdapterResult] = []
        for channel in command.channels:
            payload = payloads.get(channel)
            if payload is None:
                results.append(self._missing_payload(channel, operation))
                continue
            preview = self._payload_preview(command.product, payload)
            external_id = None
            error = None
            if operation is ProductOperation.UPDATE:
                try:
                    external_id = self._resolve_external_id(
                        command.product.sku,
                        channel,
                        command.expected_external_ids if isinstance(command, UpdateCommand) else {},
                    )
                    if not external_id:
                        error = "external id is not mapped"
                except MappingPolicyError as exc:
                    error = str(exc)
            elif operation is ProductOperation.CREATE:
                mapping = self.store.get_mapping(command.product.sku, channel)
                if mapping is not None:
                    external_id = mapping["external_id"]
                    preview["will_skip"] = True
                    preview["skip_reason"] = "stored SKU mapping already exists"
            results.append(
                AdapterResult(
                    channel=channel,
                    operation=operation,
                    success=error is None,
                    external_id=external_id,
                    error=error,
                    preview=preview,
                )
            )
        now = datetime.now(UTC)
        approval_token, approval_expires_at = self._approval_receipt(approval_sha256)
        return FanoutResult(
            job_id=f"preview-{uuid4().hex}",
            sku=command.product.sku,
            operation=operation,
            dry_run=True,
            request_sha256=request_sha256,
            approval_token=approval_token,
            approval_expires_at=approval_expires_at,
            started_at=now,
            finished_at=now,
            results=results,
        )

    def _resolved_payloads(self, command: ProductCommand) -> dict[ChannelId, ChannelPayload]:
        payloads: dict[ChannelId, ChannelPayload] = {}
        if command.profile_id:
            payloads.update(self.profiles.render(command.profile_id, command.product))
        payloads.update(command.payloads)
        return payloads

    def _preview_status_command(
        self,
        operation: ProductOperation,
        command: StatusCommand,
        request_sha256: str,
        approval_sha256: str,
    ) -> FanoutResult:
        results = []
        for channel in command.channels:
            try:
                external_id = self._resolve_external_id(
                    command.sku, channel, command.expected_external_ids
                )
                error = None if external_id else "external id is not mapped"
            except MappingPolicyError as exc:
                external_id = None
                error = str(exc)
            results.append(
                AdapterResult(
                    channel=channel,
                    operation=operation,
                    success=bool(external_id),
                    external_id=external_id,
                    error=error,
                    preview={"external_id": external_id, "operation": operation.value},
                )
            )
        now = datetime.now(UTC)
        approval_token, approval_expires_at = self._approval_receipt(approval_sha256)
        return FanoutResult(
            job_id=f"preview-{uuid4().hex}",
            sku=command.sku,
            operation=operation,
            dry_run=True,
            request_sha256=request_sha256,
            approval_token=approval_token,
            approval_expires_at=approval_expires_at,
            started_at=now,
            finished_at=now,
            results=results,
        )

    def _require_execution(
        self,
        dry_run: bool,
        confirm: str,
        approval_token: str | None,
        request_sha256: str,
    ) -> None:
        if not self.settings.mutations_enabled(dry_run=dry_run, confirm=confirm):
            raise MutationPolicyError(
                "external mutations are disabled; set KEIC_ALLOW_MUTATIONS=true, "
                "dry_run=false, and confirm='EXECUTE'"
            )
        self._verify_approval(approval_token, request_sha256)

    def _product_request_hash(
        self,
        operation: ProductOperation,
        command: ProductCommand,
        payloads: dict[ChannelId, ChannelPayload],
    ) -> str:
        expected: dict[str, str] = {}
        if isinstance(command, UpdateCommand):
            expected = {
                channel.value: external_id
                for channel, external_id in command.expected_external_ids.items()
            }
        return self._canonical_hash(
            {
                "operation": operation.value,
                "sku": command.product.sku,
                "product": command.product.model_dump(mode="json"),
                "channels": [channel.value for channel in command.channels],
                "payloads": {
                    channel.value: (
                        payloads[channel].model_dump(mode="json") if channel in payloads else None
                    )
                    for channel in command.channels
                },
                "expected_external_ids": expected,
                "idempotency_key": command.idempotency_key,
            }
        )

    def _status_request_hash(self, operation: ProductOperation, command: StatusCommand) -> str:
        return self._canonical_hash(
            {
                "operation": operation.value,
                "sku": command.sku,
                "channels": [channel.value for channel in command.channels],
                "expected_external_ids": {
                    channel.value: external_id
                    for channel, external_id in command.expected_external_ids.items()
                },
                "idempotency_key": command.idempotency_key,
            }
        )

    def _approval_request_hash(
        self, request_sha256: str, sku: str, channels: list[ChannelId]
    ) -> str:
        return self._canonical_hash(
            {
                "request_sha256": request_sha256,
                "stored_external_ids": self._mapping_snapshot(sku, channels),
            }
        )

    def _mapping_snapshot(self, sku: str, channels: list[ChannelId]) -> dict[str, str | None]:
        snapshot: dict[str, str | None] = {}
        for channel in channels:
            mapping = self.store.get_mapping(sku, channel)
            snapshot[channel.value] = str(mapping["external_id"]) if mapping is not None else None
        return snapshot

    @staticmethod
    def _canonical_hash(value: object) -> str:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(serialized.encode()).hexdigest()

    def _approval_receipt(self, request_sha256: str) -> tuple[str, datetime]:
        expires_at = int(time.time()) + self.settings.approval_ttl_seconds
        message = f"{expires_at}.{request_sha256}".encode()
        signature = hmac.new(self._approval_secret, message, hashlib.sha256).digest()
        encoded = base64.urlsafe_b64encode(signature).decode().rstrip("=")
        return (
            f"v1.{expires_at}.{encoded}",
            datetime.fromtimestamp(expires_at, UTC),
        )

    def _verify_approval(self, approval_token: str | None, request_sha256: str) -> None:
        if not approval_token:
            raise MutationPolicyError(
                "execution requires the approval_token returned by a matching preview"
            )
        try:
            version, expires_text, supplied_signature = approval_token.split(".", 2)
            expires_at = int(expires_text)
        except (TypeError, ValueError) as exc:
            raise MutationPolicyError("approval_token is malformed") from exc
        if version != "v1":
            raise MutationPolicyError("approval_token version is unsupported")
        if expires_at < int(time.time()):
            raise MutationPolicyError("approval_token has expired; preview again")
        message = f"{expires_at}.{request_sha256}".encode()
        expected = (
            base64.urlsafe_b64encode(
                hmac.new(self._approval_secret, message, hashlib.sha256).digest()
            )
            .decode()
            .rstrip("=")
        )
        if not hmac.compare_digest(supplied_signature, expected):
            raise MutationPolicyError(
                "approval_token does not match the exact request; preview again"
            )

    def _resolve_external_id(
        self, sku: str, channel: ChannelId, expected: dict[ChannelId, str]
    ) -> str | None:
        mapping = self.store.get_mapping(sku, channel)
        if mapping is None:
            if expected.get(channel):
                raise MappingPolicyError(
                    f"{channel.value} expected external id cannot bootstrap a missing mapping"
                )
            return None
        if isinstance(mapping, dict):
            mapped = mapping.get("external_id")
        else:
            mapped = getattr(mapping, "external_id", None)
        asserted = expected.get(channel)
        if asserted and asserted != mapped:
            raise MappingPolicyError(
                f"{channel.value} expected external id does not match the stored SKU mapping"
            )
        return mapped

    def _cached(
        self,
        operation: ProductOperation,
        sku: str,
        key: str,
        dry_run: bool,
        request_sha256: str,
    ) -> FanoutResult | None:
        if dry_run:
            return None
        value = self.store.get_idempotency(self._idempotency_storage_key(operation, sku, key))
        if value is None:
            return None
        result = value if isinstance(value, FanoutResult) else FanoutResult.model_validate(value)
        if result.request_sha256 and result.request_sha256 != request_sha256:
            raise IdempotencyConflictError(
                "idempotency key was already used for a different request"
            )
        return result

    def _persist_execution(self, idempotency_key: str, result: FanoutResult) -> None:
        self.store.save_job(result)
        self.store.save_idempotency(
            self._idempotency_storage_key(result.operation, result.sku, idempotency_key),
            result,
        )

    def _sku_lock(self, sku: str) -> asyncio.Lock:
        lock = self._sku_locks.get(sku)
        if lock is None:
            lock = asyncio.Lock()
            self._sku_locks[sku] = lock
        return lock

    @staticmethod
    def _idempotency_storage_key(operation: ProductOperation, sku: str, key: str) -> str:
        return json.dumps(
            [operation.value, sku, key],
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _payload_preview(product: MasterProduct, payload: ChannelPayload) -> dict:
        serialized = json.dumps(payload.body, ensure_ascii=False, sort_keys=True, default=str)
        return {
            "sku": product.sku,
            "content_type": payload.content_type,
            "payload_sha256": hashlib.sha256(serialized.encode()).hexdigest(),
            "payload_bytes": len(serialized.encode()),
            "body": IntegratedChannelService._redact_payload(payload.body),
        }

    @staticmethod
    def _redact_payload(value):
        sensitive_fragments = {
            "authorization",
            "accesskey",
            "clientsecret",
            "password",
            "secret",
            "token",
        }
        if isinstance(value, dict):
            redacted = {}
            for key, item in value.items():
                normalized = "".join(char for char in str(key).lower() if char.isalnum())
                if any(fragment in normalized for fragment in sensitive_fragments):
                    redacted[key] = "<redacted>"
                else:
                    redacted[key] = IntegratedChannelService._redact_payload(item)
            return redacted
        if isinstance(value, list):
            return [IntegratedChannelService._redact_payload(item) for item in value]
        return value

    @staticmethod
    def _missing_payload(channel: ChannelId, operation: ProductOperation) -> AdapterResult:
        return AdapterResult(
            channel=channel,
            operation=operation,
            success=False,
            error="channel payload is required",
        )

    @staticmethod
    def _missing_adapter(channel: ChannelId, operation: ProductOperation) -> AdapterResult:
        return AdapterResult(
            channel=channel,
            operation=operation,
            success=False,
            error="channel adapter is not installed",
        )

    @staticmethod
    def _missing_external_id(channel: ChannelId, operation: ProductOperation) -> AdapterResult:
        return AdapterResult(
            channel=channel,
            operation=operation,
            success=False,
            error="external id is required or must already be mapped",
        )

    @staticmethod
    def _unexpected(
        channel: ChannelId, operation: ProductOperation, error: Exception
    ) -> AdapterResult:
        return AdapterResult(
            channel=channel,
            operation=operation,
            success=False,
            retryable=False,
            error=f"{type(error).__name__}: {error}",
        )
