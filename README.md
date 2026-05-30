# AI Assist Google Docs Adapter

Dependency-light Node.js ESM bootstrap for the Google Docs connector boundary.

## MVP Boundary

This package owns Google Docs connector domain behavior:

- List Google Docs resources through an injected Google client.
- Obtain OAuth access through an injected token provider.
- Read active-resource or selected-range context.
- Reject active-resource context above 64 KiB until orchestration defines windowing or excerpt policy.
- Return connector-verified normalized context with resource revision, anchors, content hash, and provenance.
- Verify replace targets by resource revision, range resolution, and original text hash.
- Verify insert targets by resource revision and anchor resolution.
- Apply only MVP-safe `REPLACE_TEXT` and `INSERT_TEXT` mutations.
- Return typed normalized errors for validation, stale resources, conflicts, permission failures, rate limits, and provider failures.

This package intentionally has no Google SDK dependency. Production HTTP/API adapters should inject a Google client implementation and a token provider backed by the auth service token boundary.

## Conflict Behavior

The adapter validates before mutation. It does not call `applyTextMutation` when:

- The current revision differs from the expected revision.
- The target range or anchor no longer resolves.
- The current target text hash differs from `originalTextHash`.
- The mutation type is not part of the MVP replace/insert allowlist.

Those failures return typed `GoogleDocsAdapterError` values such as `RESOURCE_STALE` or `TARGET_CONFLICT` so orchestration can mark a proposed action as `CONFLICTED` without overwriting document content.

## Future API Adapters

HTTP or queue adapters should wrap this domain layer later. Those adapters should:

- Derive `tenantId` and `userId` from authenticated server-side identity.
- Retrieve OAuth tokens only through the auth/token boundary.
- Map `GoogleDocsAdapterError` to the shared platform error envelope.
- Preserve idempotency keys across apply requests.
- Keep document text, OAuth tokens, and authorization headers out of logs.
- Add real Google Docs and Drive API implementations behind the injected `googleClient` interface.

## Testing And Coverage

Run the unit tests with either command:

```sh
node --test
npm test
```

View the built-in coverage report in the terminal:

```sh
node --experimental-test-coverage --test
npm run coverage
```

The coverage command uses Node's built-in test runner and prints a text report. If later tooling writes HTML, LCOV, TAP, JUnit, or build output, those generated paths are ignored by `.gitignore`.
