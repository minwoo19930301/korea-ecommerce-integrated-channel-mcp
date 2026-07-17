from __future__ import annotations

import os
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.asyncio
async def test_stdio_handshake_lists_tools_and_runs_safe_preview(tmp_path) -> None:
    env = os.environ.copy()
    env.update(
        {
            "KEIC_ALLOW_MUTATIONS": "false",
            "KEIC_DATABASE_PATH": str(tmp_path / "mcp.sqlite3"),
            "KEIC_PROFILE_DIRECTORY": str(tmp_path / "profiles"),
            "KEIC_TRANSPORT": "stdio",
        }
    )
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "korea_ecommerce_mcp"],
        cwd=str(tmp_path),
        env=env,
    )

    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            initialize_result = await session.initialize()
            tools = await session.list_tools()
            tool_names = {tool.name for tool in tools.tools}

            preview = await session.call_tool(
                "product_publish",
                {
                    "command": {
                        "product": {
                            "sku": "MCP-SMOKE-1",
                            "name": "Safe preview",
                            "price": 1000,
                            "stock": 1,
                        },
                        "channels": ["smartstore"],
                        "payloads": {
                            "smartstore": {
                                "body": {
                                    "name": "Safe preview",
                                    "accessToken": "must-not-leak",
                                }
                            }
                        },
                        "idempotency_key": "mcp-smoke-preview-1",
                        "dry_run": True,
                        "confirm": "PREVIEW",
                    }
                },
            )

    assert initialize_result.serverInfo.name == "Korea E-commerce Integrated Channel MCP"
    assert {
        "channel_capabilities",
        "profile_preview",
        "product_publish",
        "product_update",
        "product_stop",
        "product_resume",
        "product_delete",
        "product_get",
        "operation_get",
    } <= tool_names
    assert preview.isError is False
    assert preview.structuredContent is not None
    body = preview.structuredContent["results"][0]["preview"]["body"]
    assert body["accessToken"] == "<redacted>"
