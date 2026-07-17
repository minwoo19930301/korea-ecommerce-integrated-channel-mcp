# AGENTS.md

This file is the technical operating guide for coding agents working in this repository. Keep the root README focused on the seller's problem, supported channels, and the shortest path to a preview.

## Project identity

- Product name: `Korea E-commerce Integrated Channel MCP`
- Repository and distribution: `korea-ecommerce-integrated-channel-mcp`
- Python package: `korea_ecommerce_mcp`
- Console command: `korea-ecommerce-integrated-channel-mcp`
- Environment prefix: `KEIC_`
- MCP serverInfo name: `Korea E-commerce Integrated Channel MCP`

Do not use the names of commercial multi-channel integration products in code, documentation, tests, examples, commit messages, or repository metadata. Marketplace names are allowed where they identify an actual adapter.

## Purpose and non-goals

The server accepts one seller-owned product record and coordinates product operations across SmartStore, Coupang, 11st, and ESM. It is local-first and preserves account-specific category, notice, address, delivery, and option fields in reviewed channel profiles.

It does not infer seller policy identifiers, scrape seller-center UIs, store credentials in profiles, or promise a distributed transaction across marketplaces.

## Source layout

- `src/korea_ecommerce_mcp/models.py`: canonical product and MCP command/result models
- `src/korea_ecommerce_mcp/server.py`: FastMCP tool registration
- `src/korea_ecommerce_mcp/service.py`: preview, approval, SKU locking, fan-out, and reconciliation rules
- `src/korea_ecommerce_mcp/store.py`: SQLite products, channel mappings, jobs, and completed idempotency results
- `src/korea_ecommerce_mcp/templates.py`: safe local profile loading and placeholder rendering
- `src/korea_ecommerce_mcp/adapters/`: marketplace authentication and HTTP contracts
- `profiles/`: reviewed channel payload templates; never put credentials here
- `tests/`: unit, adapter-contract, safety, persistence, and stdio MCP tests

## Core invariants

1. `MasterProduct.sku` is the internal identity. A channel mapping binds that SKU to one durable external product ID.
2. Update, stop, resume, and delete use only the stored mapping. `expected_external_ids` is an assertion and cannot bootstrap or override a mapping.
3. A create call skips channels that already have a mapping, so a partial publish can be retried with a new key without recreating successful channels.
4. Mutations for the same SKU are serialized within one process.
5. Fan-out is concurrent across selected channels but is not transactional. Record and return every channel result.
6. A successful create response without a durable external ID is uncertain, not successful. Do not create a mapping and require reconciliation before retry.
7. Do not automatically retry an external mutation after a timeout or ambiguous response.

## Mutation contract

All mutating tools default to preview mode. Execution requires all of the following:

- `KEIC_ALLOW_MUTATIONS=true`
- `dry_run=false`
- `confirm="EXECUTE"`
- the non-expired `approval_token` returned by an exact matching preview

The preview receipt binds the operation, rendered payload, SKU, channels, idempotency key, and current external-ID mapping. A profile edit or mapping change invalidates the receipt. Delete additionally requires `confirm_delete="DELETE"`.

Mutating MCP tools intentionally advertise `idempotentHint=False`. The local ledger deduplicates completed calls, but it cannot prove the outcome of a provider request if the process stops after the provider accepted it and before the response was persisted.

## MCP tools

- `channel_capabilities`
- `channel_health`
- `profile_list`
- `profile_preview`
- `product_publish`
- `product_update`
- `product_stop`
- `product_resume`
- `product_delete`
- `product_get`
- `operation_get`

Keep tool names stable unless a migration is explicitly requested. Return Pydantic models or fully typed containers so FastMCP publishes structured output schemas.

## Adapter notes

### SmartStore

Use the seller Commerce API at `api.commerce.naver.com/external`, not the NAVER Developers shopping search API. The shopping search API scheduled to close on 2026-07-31 is unrelated. The current adapter stores `originProductNo` and uses v2 product endpoints plus the product-status endpoint.

### Coupang

Store `sellerProductId`. Stop and resume first read the seller product, extract every `vendorItemId`, and update each item. Keep vendor ID checks and HMAC signing intact.

### 11st

Treat create and full update as experimental until verified against the target account contract. Require HTTPS before attaching `openapikey`. Profiles use raw XML, and placeholder values must remain XML-escaped. Endpoint paths can be overridden with `KEIC_ELEVENST_*_PATH`.

### ESM

One master goods number may represent Gmarket and Auction listings. Sell-status updates must preserve the current price, stock, and selling period returned by the provider.

## Configuration

`Settings` reads `.env` and process variables with the `KEIC_` prefix. Mutation mode requires absolute database and profile paths. Keep seller credentials out of source control and previews. Preview rendering recursively redacts common secret-bearing keys.

See `.env.example` and `docs/SETUP.md` for the complete operator-facing setup.

## Verification commands

Run from the repository root:

```bash
ruff check src tests
ruff format --check src tests
pytest -q
python -m compileall -q src tests
pip check
python -m pip wheel . --no-deps -w dist
```

The stdio integration test must initialize the MCP session, list all tools, and call a non-mutating product preview. Adapter tests must use injected HTTP transports and must never contact a real seller account.

Before publishing, scan tracked files for credentials, private local paths, stale product names, generated databases, virtual environments, and prohibited commercial-solution names. Do not commit `.env`, SQLite files, caches, or `dist/`.

## Release workflow

1. Update the user-facing README only for seller-visible behavior.
2. Put implementation rules and agent instructions in this file or `docs/ARCHITECTURE.md`.
3. Run the full verification suite.
4. Build and import-test the wheel outside the source tree.
5. Confirm the MCP serverInfo name, distribution name, CLI, environment prefix, and repository slug agree.
6. Commit only intentional source and documentation files.
