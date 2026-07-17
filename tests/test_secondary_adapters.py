from __future__ import annotations

import json

import httpx
import jwt
import pytest

from korea_ecommerce_mcp.adapters.elevenst import ElevenStreetAdapter
from korea_ecommerce_mcp.adapters.esm import EsmAdapter
from korea_ecommerce_mcp.config import Settings
from korea_ecommerce_mcp.models import ChannelPayload, MasterProduct, ProductOperation


@pytest.fixture
def product() -> MasterProduct:
    return MasterProduct(sku="SKU-1", name="Test product", price=10_000, stock=5)


@pytest.mark.asyncio
async def test_elevenstreet_create_uses_raw_xml_and_metadata_path_override(
    product: MasterProduct,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text="<Product><resultCode>200</resultCode><prdNo>11001</prdNo></Product>",
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = ElevenStreetAdapter(
        Settings(
            elevenst_api_key="seller-api-key",
            elevenst_base_url="https://seller-api.test/rest",
        ),
        client,
    )
    payload = ChannelPayload(
        body='<?xml version="1.0" encoding="UTF-8"?><Product><prdNm>Test</prdNm></Product>',
        content_type="application/xml; charset=UTF-8",
        metadata={"create_path": "/experimental/products"},
    )

    result = await adapter.create(product, payload, "create-key")

    assert result.success is True
    assert result.external_id == "11001"
    assert result.operation is ProductOperation.CREATE
    assert result.data["experimental_endpoint"] is True
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url == httpx.URL("https://seller-api.test/rest/experimental/products")
    assert request.headers["openapikey"] == "seller-api-key"
    assert request.headers["content-type"] == "application/xml; charset=UTF-8"
    assert request.content == payload.body.encode()
    assert "experimental" in " ".join(adapter.capability().notes).lower()
    await client.aclose()


@pytest.mark.asyncio
async def test_elevenstreet_update_status_and_delete_paths(
    product: MasterProduct,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text="<ClientMessage><resultCode>200</resultCode></ClientMessage>",
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = ElevenStreetAdapter(
        Settings(
            elevenst_api_key="seller-api-key",
            elevenst_base_url="https://seller-api.test/rest",
        ),
        client,
    )
    payload = ChannelPayload(
        body="<Product><prdNm>Updated</prdNm></Product>",
        metadata={"update_path": "/v2/product/{external_id}"},
    )

    updated = await adapter.update("A/B", product, payload, "update-key")
    stopped = await adapter.set_enabled("A/B", False, "stop-key")
    resumed = await adapter.set_enabled("A/B", True, "resume-key")
    deleted = await adapter.delete("A/B", "delete-key")

    assert all(item.success for item in (updated, stopped, resumed, deleted))
    assert [request.method for request in requests] == ["PUT", "PUT", "PUT", "DELETE"]
    assert [request.url.raw_path for request in requests] == [
        b"/rest/v2/product/A%2FB",
        b"/rest/prodstatservice/stat/stopdisplay/A%2FB",
        b"/rest/prodstatservice/stat/restartdisplay/A%2FB",
        b"/rest/prodservices/product/A%2FB",
    ]
    await client.aclose()


@pytest.mark.asyncio
async def test_elevenstreet_rejects_non_xml_payload_without_request(
    product: MasterProduct,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = ElevenStreetAdapter(
        Settings(
            elevenst_api_key="seller-api-key",
            elevenst_base_url="https://seller-api.test/rest",
        ),
        client,
    )

    result = await adapter.create(product, ChannelPayload(body={"not": "xml"}), "create-key")

    assert result.success is False
    assert "raw XML" in (result.error or "")
    assert calls == 0
    await client.aclose()


def esm_settings() -> Settings:
    return Settings(
        esm_master_id="master-id",
        esm_secret_key="secret-key-for-tests-that-is-long-enough",
        esm_issuer="bridge.example",
        esm_gmarket_seller_id="g-seller",
        esm_auction_seller_id="a-seller",
        esm_base_url="https://esm-api.test/item/v1",
    )


def assert_esm_token(request: httpx.Request) -> None:
    scheme, token = request.headers["authorization"].split(" ", 1)
    assert scheme == "Bearer"
    assert jwt.get_unverified_header(token)["kid"] == "master-id"
    claims = jwt.decode(
        token,
        "secret-key-for-tests-that-is-long-enough",
        algorithms=["HS256"],
        audience="sa.esmplus.com",
    )
    assert claims["iss"] == "bridge.example"
    assert claims["sub"] == "sell"
    assert claims["ssi"] == "A:a-seller,G:g-seller"
    assert isinstance(claims["iat"], int)


@pytest.mark.asyncio
async def test_esm_goods_crud_uses_official_paths_and_jwt(
    product: MasterProduct,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert_esm_token(request)
        if request.method == "POST":
            return httpx.Response(200, json={"goodsNo": 22001, "resultCode": 0})
        return httpx.Response(200, json={"goodsNo": 22001, "resultCode": 0})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = EsmAdapter(esm_settings(), client)
    create_body = {"itemBasicInfo": {"goodsName": {"kor": "Test"}}}
    update_body = {"itemBasicInfo": {"goodsName": {"kor": "Updated"}}}

    created = await adapter.create(product, ChannelPayload(body=create_body), "create-key")
    updated = await adapter.update("22001", product, ChannelPayload(body=update_body), "update-key")
    deleted = await adapter.delete("22001", "delete-key")

    assert created.success is True
    assert created.external_id == "22001"
    assert updated.success is True
    assert deleted.success is True
    assert [(request.method, request.url.path) for request in requests] == [
        ("POST", "/item/v1/goods"),
        ("PUT", "/item/v1/goods/22001"),
        ("DELETE", "/item/v1/goods/22001"),
    ]
    assert json.loads(requests[0].content) == create_body
    assert json.loads(requests[1].content) == update_body
    await client.aclose()


@pytest.mark.asyncio
async def test_esm_sell_status_preserves_commercial_fields() -> None:
    requests: list[httpx.Request] = []
    current = {
        "isSell": {"gmkt": True, "iac": True},
        "itemBasicInfo": {
            "Price": {"gmkt": 12_000, "iac": 13_000},
            "Stock": {"gmkt": 7, "iac": 8},
            "SellingPeriod": {"gmkt": -1, "iac": -1},
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert_esm_token(request)
        if request.method == "GET":
            return httpx.Response(200, json=current)
        return httpx.Response(200, json={"goodsNo": 22001, "resultCode": 0})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = EsmAdapter(esm_settings(), client)

    result = await adapter.set_enabled("22001", False, "stop-key")

    assert result.success is True
    assert result.operation is ProductOperation.STOP
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/item/v1/goods/22001/sell-status"),
        ("PUT", "/item/v1/goods/22001/sell-status"),
    ]
    sent = json.loads(requests[1].content)
    assert sent == {
        "isSell": {"gmkt": False, "iac": False},
        "itemBasicInfo": {
            "price": current["itemBasicInfo"]["Price"],
            "stock": current["itemBasicInfo"]["Stock"],
            "sellingPeriod": current["itemBasicInfo"]["SellingPeriod"],
        },
    }
    await client.aclose()


@pytest.mark.asyncio
async def test_esm_provider_error_is_not_reported_as_success(
    product: MasterProduct,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"resultCode": 1000, "message": "validation failed"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = EsmAdapter(esm_settings(), client)

    result = await adapter.create(
        product, ChannelPayload(body={"itemBasicInfo": {"x": 1}}), "create-key"
    )

    assert result.success is False
    assert result.status_code == 200
    assert result.error == "validation failed"
    await client.aclose()
