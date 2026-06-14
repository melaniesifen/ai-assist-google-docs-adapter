# AI Assist Google Docs Adapter

Dependency-free Python package for the Google Docs connector boundary.

## MVP Boundary

This package owns Google Docs connector domain behavior:

- List Google Docs resources through an injected Google client.
- Obtain OAuth access through an injected token provider.
- Read active-resource or selected-range context.
- Safely truncate active-resource context above 64 KiB with metadata about the
  original and returned byte counts.
- Return connector-verified normalized context with resource revision, anchors, content hash, and provenance.
- Verify replace targets by resource revision, range resolution, and original text hash.
- Verify insert targets by resource revision and anchor resolution.
- Apply only MVP-safe `REPLACE_TEXT` and `INSERT_TEXT` mutations.
- Return typed normalized errors for validation, stale resources, conflicts, permission failures, rate limits, and provider failures.

This package intentionally has no Google SDK dependency and no third-party Python dependency. Production HTTP/API adapters should inject a Google client implementation and a token provider backed by the auth service token boundary.

## Auth Token Handoff

The adapter never reads OAuth token records directly. For every Google operation it calls the injected token provider with:

- `tenantId`, `userId`, `provider`, `purpose`, `operation`
- operation-specific `requiredScopes`
- available `requestId`, `sessionId`, `resourceId`, `resourceRef`, `contextMode`, and `consentGrantId`

The token provider must return an auth-service handoff object with `accessToken`, `status`, `scopes`, optional `expiresAt`, and optional `googleAccountId`. Revoked or expired status returns `TOKEN_RECONNECT_REQUIRED`. Missing required scopes return `PERMISSION_DENIED` before any Google client call.

Least-privilege Google OAuth scope constants are exported by the package:

- Resource discovery: `https://www.googleapis.com/auth/drive.metadata.readonly`
- Context reads and target verification: `https://www.googleapis.com/auth/documents.readonly`
- Safe replace/insert mutation: `https://www.googleapis.com/auth/documents`

Google client implementations receive only the access token plus operation-specific request metadata. They must not log OAuth tokens, authorization headers, document text, selected text, or mutation payload text.

## Connector Contract Shapes

Resource discovery returns the shared connector resource-list result shape:
`resources` contains metadata-only resource refs with `connector`,
`resourceId`, `resourceType`, optional `displayName`, optional `externalUrl`,
and adapter-local revision metadata. It does not return document text.

Read context returns the shared connector read-context result shape:
`context` contains normalized connector-verified context and
`resourceRevision` repeats the provider revision used for the read. The nested
context uses shared `resourceRef` fields, object-shaped anchor metadata for the
current Python context-service handoff, content hash, provenance,
capture/expiry timestamps, and metadata-only revision details.

## Conflict Behavior

The adapter validates before mutation. It does not call `applyTextMutation` when:

- The current revision differs from the expected revision.
- The target range or anchor no longer resolves.
- The current target text hash differs from `originalTextHash`.
- The mutation type is not part of the MVP replace/insert allowlist.

Those failures return typed `GoogleDocsAdapterError` values such as `RESOURCE_STALE` or `TARGET_CONFLICT` so orchestration can mark a proposed action as `CONFLICTED` without overwriting document content.

## Timeout And Retry Policy

Adapter calls use a bounded operation timeout, defaulting to 10 seconds.

- Resource listing, context reads, and target verification retry retryable provider failures once.
- Mutation writes are not retried by this adapter. Orchestration must reconcile idempotency and action state before any repeat write attempt.
- Timeouts return `PROVIDER_TIMEOUT`; mutation timeouts are not marked retryable because the provider write result is uncertain.
- Revoked or expired token-provider failures return `TOKEN_RECONNECT_REQUIRED`.
- Unsupported mutation types return `UNSUPPORTED_MUTATION` before provider mutation.

## Metadata-Only Logging Rules

This package does not create logs. Future HTTP, queue, or internal-service wrappers around it may log only metadata: request ID, tenant/user IDs, operation name, provider, status, error code, retryability, and latency.

Wrappers must not log OAuth tokens, authorization headers, document text, selected text, replacement or insertion text, decrypted action payloads, prompts, model responses, or raw Google provider payloads.

## Future API Adapters

HTTP or queue adapters should wrap this domain layer later. Those adapters should:

- Derive `tenantId` and `userId` from authenticated server-side identity.
- Retrieve OAuth tokens only through the auth/token boundary.
- Map `GoogleDocsAdapterError` to the shared platform error envelope.
- Preserve idempotency keys across apply requests.
- Keep document text, OAuth tokens, and authorization headers out of logs.
- Add real Google Docs and Drive API implementations behind the injected `googleClient` interface.

## Task Breakdown

Implementation tasks are tracked in [TASKS.md](TASKS.md). Update the checkboxes there in the same change that implements or verifies a task.

## Testing

Run the unit tests with the standard library test runner:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests
PYTHONPATH=src python3 -m compileall src tests
```

No virtual environment or package install is required for the current local test suite. If later work adds third-party libraries, add repo-local dependency manifests and document the install command in this section.

If later tooling writes coverage, cache, dependency, virtualenv, or build output, those generated paths are ignored by `.gitignore`.
