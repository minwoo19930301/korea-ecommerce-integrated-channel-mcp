# Verification record

Date: 2026-07-18

## Automated checks

- `pytest -q`: 38 passed
- `ruff check src tests`: passed
- `ruff format --check src tests`: passed
- `python -m compileall -q src tests`: passed
- `pip check`: no broken requirements
- MCP stdio integration: initialize, list 11 tools, and call a safe product preview
- Built wheel import: package loads and registers all 11 tools outside the source tree
- Distribution: `korea-ecommerce-integrated-channel-mcp 0.2.0`
- Wheel SHA-256: `d1fe244e41ba428504fbff8b33b7d2b557dfa25a40f821262426486fa2bcc491`

HTTP adapter tests use injected transports and cover authentication, official
request paths, response parsing, lower-level item fan-out, partial failures,
and configuration errors without touching a seller account.

## Safety checks

- Mutations default off.
- Execution requires a preview-bound, expiring approval receipt.
- Hard delete has an additional confirmation phrase.
- Update, status, and delete cannot override the stored external product ID.
- Same-SKU mutations are serialized within one server process.
- Plaintext HTTP is rejected before the 11st API key is attached.
- XML profile values are escaped.
- Preview output redacts common secret-bearing fields.
- A successful create response without a durable product number is marked
  uncertain and is not mapped as a success.

## Live-validation boundary

No seller credentials were available during this build, so no real product was
created, changed, stopped, or deleted. Before production use, prepare an
account-specific payload profile and run a disposable one-product canary on
each channel while mutations remain tightly controlled. The 11st connector is
experimental until its routes and XML contract are confirmed for the target
seller account.

## SmartStore API status check

The adapter uses the seller Commerce API at `api.commerce.naver.com/external`.
The NAVER Developers shopping search API scheduled to close on 2026-07-31 is a
different service. The seller Commerce API documentation was current at
version 2.82.0 dated 2026-07-07 when this release was prepared.
