from __future__ import annotations

import base64
import json
from urllib.parse import parse_qs

import bcrypt
import httpx
import pytest

from korea_ecommerce_mcp.adapters.coupang import CoupangAdapter
from korea_ecommerce_mcp.adapters.naver import NaverAdapter
from korea_ecommerce_mcp.config import Settings
from korea_ecommerce_mcp.models import ChannelPayload, MasterProduct, ProductOperation


@pytest.fixture
def product() -> MasterProduct:
    return MasterProduct(sku="SKU-001", name="Test product", price=10_000, stock=5)


@pytest.fixture
def naver_secret() -> str:
    # Production secrets are pre-generated bcrypt salts; low rounds keep this test fast.
    return bcrypt.gensalt(rounds=4).decode()


@pytest.mark.asyncio
async def test_naver_oauth_and_product_crud_use_official_paths(
    product: MasterProduct, naver_secret: str
) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/external/v1/oauth2/token":
            form = parse_qs(request.content.decode())
            assert form["client_id"] == ["client-id"]
            assert form["grant_type"] == ["client_credentials"]
            assert form["type"] == ["SELLER"]
            assert form["account_id"] == ["seller-account"]
            timestamp = form["timestamp"][0]
            decoded_signature = base64.standard_b64decode(form["client_secret_sign"][0])
            assert decoded_signature == bcrypt.hashpw(
                f"client-id_{timestamp}".encode(), naver_secret.encode()
            )
            return httpx.Response(200, json={"access_token": "oauth-token", "expires_in": 3600})

        assert request.headers["Authorization"] == "Bearer oauth-token"
        if request.method == "POST" and request.url.path == "/external/v2/products":
            assert json.loads(request.content) == {"originProduct": {"name": "created"}}
            return httpx.Response(200, json={"originProductNo": 123456})
        if (
            request.method == "PUT"
            and request.url.path == "/external/v2/products/origin-products/123456"
        ):
            assert json.loads(request.content) == {"originProduct": {"name": "updated"}}
            return httpx.Response(200, json={"originProductNo": 123456})
        if request.url.path.endswith("/origin-products/123456/change-status"):
            status = json.loads(request.content)["statusType"]
            assert status in {"SALE", "SUSPENSION"}
            return httpx.Response(200, json={"statusType": status})
        if (
            request.method == "DELETE"
            and request.url.path == "/external/v2/products/origin-products/123456"
        ):
            return httpx.Response(200, json={"code": "SUCCESS"})
        return httpx.Response(404, json={"code": "NOT_FOUND"})

    settings = Settings(
        naver_client_id="client-id",
        naver_client_secret=naver_secret,
        naver_account_id="seller-account",
        naver_base_url="https://naver.test/external",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = NaverAdapter(settings, client)
        created = await adapter.create(
            product,
            ChannelPayload(body={"originProduct": {"name": "created"}}),
            "create-key",
        )
        updated = await adapter.update(
            "123456",
            product,
            ChannelPayload(body={"originProduct": {"name": "updated"}}),
            "update-key",
        )
        stopped = await adapter.set_enabled("123456", False, "stop-key-1")
        resumed = await adapter.set_enabled("123456", True, "resume-key")
        deleted = await adapter.delete("123456", "delete-key")

    assert created.success and created.external_id == "123456"
    assert updated.success and updated.operation is ProductOperation.UPDATE
    assert stopped.success and stopped.operation is ProductOperation.STOP
    assert resumed.success and resumed.operation is ProductOperation.RESUME
    assert deleted.success and deleted.operation is ProductOperation.DELETE
    assert sum(request.url.path.endswith("/oauth2/token") for request in calls) == 1


@pytest.mark.asyncio
async def test_naver_refreshes_token_once_on_gateway_authn(
    product: MasterProduct, naver_secret: str
) -> None:
    token_calls = 0
    product_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls, product_calls
        if request.url.path.endswith("/oauth2/token"):
            token_calls += 1
            return httpx.Response(
                200,
                json={"access_token": f"token-{token_calls}", "expires_in": 3600},
            )
        product_calls += 1
        if product_calls == 1:
            assert request.headers["Authorization"] == "Bearer token-1"
            return httpx.Response(401, json={"code": "GW.AUTHN"})
        assert request.headers["Authorization"] == "Bearer token-2"
        return httpx.Response(200, json={"originProductNo": 77})

    settings = Settings(
        naver_client_id="client-id",
        naver_client_secret=naver_secret,
        naver_base_url="https://naver.test/external",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await NaverAdapter(settings, client).create(
            product,
            ChannelPayload(body={"originProduct": {"name": "created"}}),
            "create-key",
        )

    assert result.success
    assert token_calls == 2
    assert product_calls == 2


@pytest.mark.asyncio
async def test_coupang_hmac_product_crud_and_vendor_item_status(
    product: MasterProduct,
) -> None:
    calls: list[httpx.Request] = []
    product_path = "/v2/providers/seller_api/apis/api/v1/marketplace/seller-products"
    item_path = "/v2/providers/seller_api/apis/api/v1/marketplace/vendor-items"

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        authorization = request.headers["Authorization"]
        signed_date = authorization.split("signed-date=", 1)[1].split(",", 1)[0]
        query = request.url.query.decode()
        assert authorization == CoupangAdapter.generate_authorization(
            request.method,
            request.url.path,
            query,
            "access-key",
            "secret-key",
            signed_date,
        )
        assert request.headers["X-Requested-By"] == "A00012345"
        assert request.headers["X-MARKET"] == "KR"

        if request.method == "GET" and request.url.path == product_path:
            assert parse_qs(query) == {
                "vendorId": ["A00012345"],
                "maxPerPage": ["1"],
            }
            return httpx.Response(200, json={"code": "SUCCESS", "data": []})
        if request.method == "GET" and request.url.path == f"{product_path}/987654321":
            return httpx.Response(
                200,
                json={
                    "code": "SUCCESS",
                    "data": {
                        "sellerProductId": 987654321,
                        "items": [
                            {"vendorItemId": 555000},
                            {"vendorItemId": 555001},
                        ],
                    },
                },
            )
        if request.method == "POST" and request.url.path == product_path:
            body = json.loads(request.content)
            assert body == {"sellerProductName": "created", "vendorId": "A00012345"}
            return httpx.Response(
                200,
                json={
                    "code": "200",
                    "data": {"code": "SUCCESS", "data": 987654321},
                },
            )
        if request.method == "PUT" and request.url.path == product_path:
            body = json.loads(request.content)
            assert body == {
                "sellerProductName": "updated",
                "sellerProductId": 987654321,
                "vendorId": "A00012345",
            }
            return httpx.Response(200, json={"code": "SUCCESS", "data": 987654321})
        if request.url.path in {
            f"{item_path}/555000/sales/stop",
            f"{item_path}/555001/sales/stop",
        }:
            return httpx.Response(200, json={"code": "SUCCESS"})
        if request.url.path in {
            f"{item_path}/555000/sales/resume",
            f"{item_path}/555001/sales/resume",
        }:
            return httpx.Response(200, json={"code": "SUCCESS"})
        if request.method == "DELETE" and request.url.path == f"{product_path}/987654321":
            return httpx.Response(200, json={"code": "SUCCESS", "data": "987654321"})
        return httpx.Response(404, json={"code": "ERROR", "message": "not found"})

    settings = Settings(
        coupang_vendor_id="A00012345",
        coupang_access_key="access-key",
        coupang_secret_key="secret-key",
        coupang_base_url="https://coupang.test",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = CoupangAdapter(settings, client)
        health = await adapter.health()
        created = await adapter.create(
            product,
            ChannelPayload(body={"sellerProductName": "created"}),
            "create-key",
        )
        updated = await adapter.update(
            "987654321",
            product,
            ChannelPayload(body={"sellerProductName": "updated"}),
            "update-key",
        )
        stopped = await adapter.set_enabled("987654321", False, "stop-key-1")
        resumed = await adapter.set_enabled("987654321", True, "resume-key")
        deleted = await adapter.delete("987654321", "delete-key")

    assert health["ok"] is True
    assert created.success and created.external_id == "987654321"
    assert updated.success and updated.external_id == "987654321"
    assert stopped.success and stopped.operation is ProductOperation.STOP
    assert resumed.success and resumed.operation is ProductOperation.RESUME
    assert len(stopped.data["vendor_item_results"]) == 2
    assert len(resumed.data["vendor_item_results"]) == 2
    assert deleted.success and deleted.operation is ProductOperation.DELETE
    assert len(calls) == 10


@pytest.mark.asyncio
async def test_coupang_status_reports_partial_vendor_item_failure() -> None:
    product_path = "/v2/providers/seller_api/apis/api/v1/marketplace/seller-products"
    item_path = "/v2/providers/seller_api/apis/api/v1/marketplace/vendor-items"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"{product_path}/123":
            return httpx.Response(
                200,
                json={
                    "code": "SUCCESS",
                    "data": {
                        "items": [
                            {"vendorItemId": 10},
                            {"vendorItemId": 11},
                        ]
                    },
                },
            )
        if request.url.path == f"{item_path}/10/sales/stop":
            return httpx.Response(200, json={"code": "SUCCESS"})
        if request.url.path == f"{item_path}/11/sales/stop":
            return httpx.Response(503, json={"code": "ERROR", "message": "retry"})
        return httpx.Response(404, json={"code": "ERROR"})

    settings = Settings(
        coupang_vendor_id="A00012345",
        coupang_access_key="access-key",
        coupang_secret_key="secret-key",
        coupang_base_url="https://coupang.test",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await CoupangAdapter(settings, client).set_enabled("123", False, "stop-key-1")

    assert not result.success
    assert result.external_id == "123"
    assert result.status_code == 503
    assert result.retryable is True
    assert [item["success"] for item in result.data["vendor_item_results"]] == [
        True,
        False,
    ]


@pytest.mark.asyncio
async def test_missing_credentials_never_reach_transport(product: MasterProduct) -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(500, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        naver = NaverAdapter(Settings(), client)
        coupang = CoupangAdapter(Settings(), client)
        naver_health = await naver.health()
        coupang_health = await coupang.health()
        naver_result = await naver.create(
            product, ChannelPayload(body={"originProduct": {"name": "x"}}), "key-12345"
        )
        coupang_result = await coupang.create(
            product, ChannelPayload(body={"sellerProductName": "x"}), "key-12345"
        )

    assert naver_health["configured"] is False
    assert coupang_health["configured"] is False
    assert not naver_result.success
    assert not coupang_result.success
    assert call_count == 0


@pytest.mark.asyncio
async def test_coupang_rejects_cross_vendor_payload_before_transport(
    product: MasterProduct,
) -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, request=request, json={"code": "SUCCESS"})

    settings = Settings(
        coupang_vendor_id="A00012345",
        coupang_access_key="access-key",
        coupang_secret_key="secret-key",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await CoupangAdapter(settings, client).create(
            product,
            ChannelPayload(body={"sellerProductName": "x", "vendorId": "A99999999"}),
            "key-12345",
        )

    assert not result.success
    assert "does not match" in (result.error or "")
    assert call_count == 0
