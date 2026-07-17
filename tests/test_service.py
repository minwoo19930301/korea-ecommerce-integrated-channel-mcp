from __future__ import annotations

import asyncio
from typing import Any

import pytest

from korea_ecommerce_mcp.adapters.base import ChannelAdapter
from korea_ecommerce_mcp.config import Settings
from korea_ecommerce_mcp.models import (
    AdapterResult,
    ChannelCapability,
    ChannelId,
    ChannelPayload,
    DeleteCommand,
    MasterProduct,
    ProductCommand,
    ProductOperation,
)
from korea_ecommerce_mcp.service import (
    IdempotencyConflictError,
    IntegratedChannelService,
    MutationPolicyError,
)
from korea_ecommerce_mcp.store import SQLiteStore


class FakeAdapter(ChannelAdapter):
    def __init__(self, channel: ChannelId, *, fail: bool = False) -> None:
        self.channel = channel
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    async def close(self) -> None:
        return None

    def capability(self) -> ChannelCapability:
        return ChannelCapability(channel=self.channel, configured=True)

    async def health(self) -> dict[str, Any]:
        return {"configured": True, "ok": True}

    async def create(
        self, product: MasterProduct, payload: ChannelPayload, idempotency_key: str
    ) -> AdapterResult:
        await asyncio.sleep(0)
        self.calls.append(("create", idempotency_key))
        if self.fail:
            return AdapterResult(
                channel=self.channel,
                operation=ProductOperation.CREATE,
                success=False,
                error="fixture failure",
            )
        return AdapterResult(
            channel=self.channel,
            operation=ProductOperation.CREATE,
            success=True,
            external_id=f"{self.channel.value}-{product.sku}",
            status_code=200,
        )

    async def update(
        self,
        external_id: str,
        product: MasterProduct,
        payload: ChannelPayload,
        idempotency_key: str,
    ) -> AdapterResult:
        self.calls.append(("update", idempotency_key))
        return AdapterResult(
            channel=self.channel,
            operation=ProductOperation.UPDATE,
            success=True,
            external_id=external_id,
        )

    async def set_enabled(
        self, external_id: str, enabled: bool, idempotency_key: str
    ) -> AdapterResult:
        operation = ProductOperation.RESUME if enabled else ProductOperation.STOP
        self.calls.append((operation.value, idempotency_key))
        return AdapterResult(
            channel=self.channel,
            operation=operation,
            success=True,
            external_id=external_id,
        )

    async def delete(self, external_id: str, idempotency_key: str) -> AdapterResult:
        self.calls.append(("delete", idempotency_key))
        return AdapterResult(
            channel=self.channel,
            operation=ProductOperation.DELETE,
            success=True,
            external_id=external_id,
        )


def product() -> MasterProduct:
    return MasterProduct(sku="SKU-1", name="Test", price=1000, stock=3)


def command(*, dry_run: bool, confirm: str) -> ProductCommand:
    return ProductCommand(
        product=product(),
        channels=[ChannelId.SMARTSTORE, ChannelId.COUPANG],
        payloads={
            ChannelId.SMARTSTORE: ChannelPayload(body={"name": "Test"}),
            ChannelId.COUPANG: ChannelPayload(body={"name": "Test"}),
        },
        idempotency_key="publish-SKU-1-v1",
        dry_run=dry_run,
        confirm=confirm,
    )


def service(tmp_path, *, allow_mutations: bool = True, fail_coupang: bool = False):
    settings = Settings(
        allow_mutations=allow_mutations,
        database_path=tmp_path / "store.sqlite3",
        profile_directory=tmp_path / "profiles",
    )
    naver = FakeAdapter(ChannelId.SMARTSTORE)
    coupang = FakeAdapter(ChannelId.COUPANG, fail=fail_coupang)
    store = SQLiteStore(settings.database_path)
    instance = IntegratedChannelService(
        settings=settings,
        store=store,
        adapters={ChannelId.SMARTSTORE: naver, ChannelId.COUPANG: coupang},
    )
    return instance, store, naver, coupang


async def approve_publish(
    instance: IntegratedChannelService, request: ProductCommand
) -> ProductCommand:
    preview_request = request.model_copy(
        update={"dry_run": True, "confirm": "PREVIEW", "approval_token": None}
    )
    preview = await instance.publish(preview_request)
    assert preview.approval_token
    return request.model_copy(update={"approval_token": preview.approval_token})


async def approve_delete(
    instance: IntegratedChannelService, request: DeleteCommand
) -> DeleteCommand:
    preview_request = request.model_copy(
        update={"dry_run": True, "confirm": "PREVIEW", "approval_token": None}
    )
    preview = await instance.delete(preview_request)
    assert preview.approval_token
    return request.model_copy(update={"approval_token": preview.approval_token})


async def test_preview_has_no_external_or_local_mutation(tmp_path):
    instance, store, naver, coupang = service(tmp_path)

    result = await instance.publish(command(dry_run=True, confirm="PREVIEW"))

    assert result.dry_run is True
    assert result.all_succeeded is True
    assert naver.calls == []
    assert coupang.calls == []
    assert store.get_product("SKU-1") is None


async def test_preview_redacts_nested_secret_fields(tmp_path):
    instance, _, _, _ = service(tmp_path)
    request = command(dry_run=True, confirm="PREVIEW")
    request.payloads[ChannelId.SMARTSTORE] = ChannelPayload(
        body={
            "name": "Test",
            "accessToken": "top-secret",
            "nested": [{"client_secret": "also-secret", "safe": "visible"}],
        }
    )

    result = await instance.publish(request)

    body = result.results[0].preview["body"]
    assert body["accessToken"] == "<redacted>"
    assert body["nested"][0]["client_secret"] == "<redacted>"
    assert body["nested"][0]["safe"] == "visible"


async def test_execute_fans_out_persists_mappings_and_is_idempotent(tmp_path):
    instance, store, naver, coupang = service(tmp_path)
    request = await approve_publish(instance, command(dry_run=False, confirm="EXECUTE"))

    first = await instance.publish(request)
    second = await instance.publish(request)

    assert first == second
    assert first.all_succeeded is True
    assert naver.calls == [("create", request.idempotency_key)]
    assert coupang.calls == [("create", request.idempotency_key)]
    assert store.get_product("SKU-1") == product()
    assert len(store.list_mappings("SKU-1")) == 2
    assert store.get_job(first.job_id) == first


async def test_concurrent_duplicate_publish_executes_each_channel_once(tmp_path):
    instance, _, naver, coupang = service(tmp_path)
    request = await approve_publish(instance, command(dry_run=False, confirm="EXECUTE"))

    first, second = await asyncio.gather(
        instance.publish(request),
        instance.publish(request),
    )

    assert first == second
    assert naver.calls == [("create", request.idempotency_key)]
    assert coupang.calls == [("create", request.idempotency_key)]


async def test_preview_approval_is_bound_to_exact_product_request(tmp_path):
    instance, _, naver, coupang = service(tmp_path)
    original = await approve_publish(instance, command(dry_run=False, confirm="EXECUTE"))
    changed = original.model_copy(
        update={"product": original.product.model_copy(update={"price": 2000})}
    )

    with pytest.raises(MutationPolicyError, match="does not match the exact request"):
        await instance.publish(changed)

    assert naver.calls == []
    assert coupang.calls == []


async def test_reusing_idempotency_key_for_different_request_is_rejected(tmp_path):
    instance, _, _, _ = service(tmp_path)
    first = await approve_publish(instance, command(dry_run=False, confirm="EXECUTE"))
    await instance.publish(first)

    changed = command(dry_run=False, confirm="EXECUTE")
    changed.product = changed.product.model_copy(update={"price": 2000})
    changed = await approve_publish(instance, changed)

    with pytest.raises(IdempotencyConflictError, match="different request"):
        await instance.publish(changed)


async def test_partial_failure_does_not_erase_success(tmp_path):
    instance, store, _, _ = service(tmp_path, fail_coupang=True)

    request = await approve_publish(instance, command(dry_run=False, confirm="EXECUTE"))
    result = await instance.publish(request)

    assert [item.success for item in result.results] == [True, False]
    assert [item["channel"] for item in store.list_mappings("SKU-1")] == ["smartstore"]


async def test_partial_publish_retry_skips_already_mapped_channel(tmp_path):
    instance, _, naver, coupang = service(tmp_path, fail_coupang=True)
    first_request = await approve_publish(instance, command(dry_run=False, confirm="EXECUTE"))
    first = await instance.publish(first_request)
    assert [item.success for item in first.results] == [True, False]

    coupang.fail = False
    retry = command(dry_run=False, confirm="EXECUTE").model_copy(
        update={"idempotency_key": "publish-SKU-1-v2"}
    )
    retry = await approve_publish(instance, retry)
    second = await instance.publish(retry)

    assert second.all_succeeded is True
    assert second.results[0].data["skipped"] is True
    assert naver.calls == [("create", "publish-SKU-1-v1")]
    assert coupang.calls == [
        ("create", "publish-SKU-1-v1"),
        ("create", "publish-SKU-1-v2"),
    ]


async def test_execute_requires_server_switch(tmp_path):
    instance, _, _, _ = service(tmp_path, allow_mutations=False)

    with pytest.raises(MutationPolicyError, match="external mutations are disabled"):
        await instance.publish(command(dry_run=False, confirm="EXECUTE"))


async def test_delete_requires_explicit_delete_phrase(tmp_path):
    instance, store, _, _ = service(tmp_path)
    store.save_product(product())
    store.upsert_mapping("SKU-1", ChannelId.SMARTSTORE, "external-1", "active")
    request = DeleteCommand(
        sku="SKU-1",
        channels=[ChannelId.SMARTSTORE],
        idempotency_key="delete-SKU-1-v1",
        dry_run=False,
        confirm="EXECUTE",
        confirm_delete="NO",
    )
    request = await approve_delete(instance, request)

    with pytest.raises(MutationPolicyError, match="confirm_delete"):
        await instance.delete(request)


async def test_delete_expected_id_must_match_stored_mapping(tmp_path):
    instance, store, naver, _ = service(tmp_path)
    store.save_product(product())
    store.upsert_mapping("SKU-1", ChannelId.SMARTSTORE, "external-1", "active")
    request = DeleteCommand(
        sku="SKU-1",
        channels=[ChannelId.SMARTSTORE],
        expected_external_ids={ChannelId.SMARTSTORE: "different-product"},
        idempotency_key="delete-SKU-1-mismatch",
        dry_run=False,
        confirm="EXECUTE",
        confirm_delete="DELETE",
    )
    request = await approve_delete(instance, request)

    result = await instance.delete(request)

    assert result.results[0].success is False
    assert "does not match" in (result.results[0].error or "")
    assert naver.calls == []
    assert store.get_job(result.job_id) == result
