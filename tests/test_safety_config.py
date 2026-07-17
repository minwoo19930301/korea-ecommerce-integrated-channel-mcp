from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from korea_ecommerce_mcp.adapters.elevenst import ElevenStreetAdapter
from korea_ecommerce_mcp.config import Settings
from korea_ecommerce_mcp.models import ChannelPayload, MasterProduct


def test_mutation_mode_rejects_cwd_relative_runtime_paths() -> None:
    with pytest.raises(ValidationError, match="mutation mode requires absolute paths"):
        Settings(
            allow_mutations=True,
            database_path="relative.sqlite3",
            profile_directory="profiles",
        )


@pytest.mark.asyncio
async def test_elevenst_rejects_plaintext_http_before_sending_credentials() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, text="<result><resultCode>0</resultCode></result>")

    settings = Settings(
        elevenst_api_key="seller-secret",
        elevenst_base_url="http://api.example.test/rest",
    )
    product = MasterProduct(sku="SKU-1", name="Test", price=1000, stock=1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await ElevenStreetAdapter(settings, client).create(
            product,
            ChannelPayload(body="<Product><name>Test</name></Product>"),
            "create-SKU-1",
        )

    assert result.success is False
    assert "HTTPS" in (result.error or "")
    assert calls == []
