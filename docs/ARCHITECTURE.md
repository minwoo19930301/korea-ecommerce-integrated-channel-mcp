# Architecture

## Core flow

```text
MCP client
  -> product_publish / update / stop / resume / delete
    -> mutation policy + preview-bound approval receipt
    -> per-SKU execution lock + completed-result idempotency gate
      -> canonical MasterProduct
      -> reviewed payload profile + per-call overrides
        -> concurrent channel adapters
          -> official seller APIs
        -> per-channel result ledger
      -> SKU <-> external product identifier mapping
```

## Why a canonical product is not enough by itself

Name, price, stock, description, and images can be normalized. Category IDs,
delivery and return policies, legal notices, brand approval, option attributes,
and seller addresses are channel- and account-specific. The server therefore
keeps two layers:

1. `MasterProduct`: the seller's source of truth.
2. A reviewed local profile: the exact official request shape for each channel.

Profiles contain placeholders such as `{{product.name}}` and preserve native
JSON number/list/object types when a placeholder occupies the whole value.
Per-call payloads override a profile for exceptional products.

## Fan-out semantics

Fan-out is concurrent but not transactional. A successful registration cannot
be rolled back reliably merely because another channel failed. Every result is
stored independently, and the caller receives a job ID for reconciliation.

Mutation calls are never automatically retried. The idempotency ledger returns
the first recorded result for an operation/key pair. When a network timeout
makes the result uncertain, query the affected channel before using a new key.

A preview hashes the rendered channel payloads, operation, SKU, selected
channels, idempotency key, and current external-ID mapping. The server signs
that state with a short-lived in-memory secret. Execution must present the
receipt for the exact same request. A restart invalidates outstanding receipts.

The server serializes mutations for the same SKU in one process. On a new
registration key, an existing channel mapping is treated as a safe skip, which
allows a partial fan-out to be retried without recreating already mapped
channels. This is not a distributed transaction: a process crash after the
provider accepted a request but before local persistence still requires manual
channel reconciliation.

## Identifier model

The internal key is the seller SKU. Each connector maps it to its durable
channel product identifier. Some channels also expose option- or site-level
identifiers; the adapter resolves those internally when an operation applies at
that lower level.

Callers cannot bootstrap or override a mapping through an update, status, or
delete call. Optional `expected_external_ids` values are assertions and must
match the stored mapping.

## Adapter maturity

- SmartStore: v2 product create/update/delete plus product status.
- Coupang: seller-product create/update/delete; stop/resume fans out to product items.
- ESM: master-goods create/update/delete and combined site sale status.
- 11st: raw XML connector with configurable Seller API paths; treat as experimental
  until the target account's current contract and response shapes are captured.

No live seller mutation is part of the automated test suite. Tests use injected
HTTP transports and must be followed by a controlled seller-account canary.
