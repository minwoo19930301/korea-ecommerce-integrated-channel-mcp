from __future__ import annotations

from functools import lru_cache
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from korea_ecommerce_mcp.config import Settings
from korea_ecommerce_mcp.models import (
    DeleteCommand,
    FanoutResult,
    MasterProduct,
    ProductCommand,
    StatusCommand,
    UpdateCommand,
)
from korea_ecommerce_mcp.registry import build_adapters
from korea_ecommerce_mcp.service import IntegratedChannelService
from korea_ecommerce_mcp.store import SQLiteStore

mcp = FastMCP(
    "Korea E-commerce Integrated Channel MCP",
    instructions=(
        "Fan out product operations across configured seller channels. "
        "Always preview first and reuse its approval_token without changing the request. "
        "External writes require the server mutation switch, dry_run=false, and "
        "confirm=EXECUTE. Hard delete additionally requires confirm_delete=DELETE. "
        "Never invent channel category, policy, address, notice, or product IDs."
    ),
    json_response=True,
)


@lru_cache(maxsize=1)
def get_service() -> IntegratedChannelService:
    settings = Settings()
    store = SQLiteStore(settings.database_path)
    return IntegratedChannelService(
        settings=settings,
        store=store,
        adapters=build_adapters(settings),
    )


@mcp.tool(
    title="List seller-channel capabilities",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
def channel_capabilities() -> list[dict[str, Any]]:
    """Show installed connectors, configuration state, and supported operations."""
    return get_service().capabilities()


@mcp.tool(
    title="List reviewed channel payload profiles",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
def profile_list() -> list[dict[str, str]]:
    """List local payload profiles that can turn one master product into channel bodies."""
    return get_service().list_profiles()


@mcp.tool(
    title="Preview a payload profile",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
def profile_preview(profile_id: str, product: MasterProduct) -> dict[str, Any]:
    """Render a reviewed profile without calling any external seller API."""
    return get_service().preview_profile(profile_id, product)


@mcp.tool(
    title="Check seller-channel health",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def channel_health() -> dict[str, dict[str, Any]]:
    """Report configuration and connector-supported checks; inspect network_checked."""
    return await get_service().health()


@mcp.tool(
    title="Publish one product to multiple channels",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def product_publish(command: ProductCommand) -> FanoutResult:
    """Preview or create one master product across all selected channels in parallel."""
    return await get_service().publish(command)


@mcp.tool(
    title="Update one product across multiple channels",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def product_update(command: UpdateCommand) -> FanoutResult:
    """Preview or update a mapped product across selected channels in parallel."""
    return await get_service().update(command)


@mcp.tool(
    title="Stop product sales across channels",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def product_stop(command: StatusCommand) -> FanoutResult:
    """Preview or stop a product on every selected channel."""
    return await get_service().set_enabled(command, enabled=False)


@mcp.tool(
    title="Resume product sales across channels",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def product_resume(command: StatusCommand) -> FanoutResult:
    """Preview or resume a product on every selected channel."""
    return await get_service().set_enabled(command, enabled=True)


@mcp.tool(
    title="Delete a product across channels",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def product_delete(command: DeleteCommand) -> FanoutResult:
    """Preview or permanently delete a mapped product across selected channels."""
    return await get_service().delete(command)


@mcp.tool(
    title="Get a master product and channel mappings",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
def product_get(sku: str) -> dict[str, Any]:
    """Read the canonical product and its per-channel external identifiers."""
    return get_service().get_product(sku)


@mcp.tool(
    title="Get a fan-out operation result",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
def operation_get(job_id: str) -> dict[str, Any] | None:
    """Read a persisted operation, including per-channel partial failures."""
    return get_service().get_job(job_id)
