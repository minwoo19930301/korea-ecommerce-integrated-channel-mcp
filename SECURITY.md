# Security and operating policy

## Credentials

- Keep seller credentials only in `.env`, a process secret store, or injected environment variables.
- Do not place credentials in product payload profiles.
- Restrict the runtime host to the IP addresses approved by each seller channel.

## Mutation gates

External writes require all of the following:

1. `KEIC_ALLOW_MUTATIONS=true`
2. `dry_run=false`
3. `confirm="EXECUTE"`
4. A non-expired `approval_token` returned by a matching preview

Hard deletion also requires `confirm_delete="DELETE"`.

## Operating sequence

1. Run a payload preview and review every channel result.
2. Execute the unchanged request with its approval token.
3. Read the current channel object before update, stop, or deletion.
4. Use a unique idempotency key for the intended change.
5. Inspect every channel result; partial success is expected and is not rolled back.
6. If an API response is ambiguous, query the channel before retrying.

The server never retries product creation or another external mutation automatically.
Completed calls are cached, but no local ledger can prove the outcome of a
provider request interrupted before its response was persisted.

## Identifier and transport policy

- Update, status, and delete operations only use the external ID stored for the SKU.
- `expected_external_ids` can assert that mapping but cannot replace or create it.
- The 11st connector refuses non-HTTPS base URLs before attaching its API key.
- Mutation mode requires absolute database and profile paths.
