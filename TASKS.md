# Task Breakdown

Update this file as implementation progresses. Check off completed tasks in the same change that implements them.

Canonical cross-repo tasks live in `../ai-assist-architecture/implementation-task-breakdown.md`. This repo owns the Google Docs adapter portions of `DOCS-*`, `CTX-005`, `ACTION-*`, `AUTH-003`, `OPS-003`, and `REPO-001` items, grounded by `../ai-assist-architecture/lld-context-connectors.md` and `../ai-assist-architecture/lld-actions-writeback.md`.

Migration status: The repo has been migrated from the temporary JavaScript ESM bootstrap to Python for the current local package scope. Broad new feature work may continue in Python after the parent migration checkpoint.

## Completed Bootstrap

- [x] REPO-001 bootstrap: create dependency-light Node.js ESM package with direct `node:test` coverage commands.
- [x] AUTH-003 repo-local: define injected token-provider boundary without plaintext token storage in this package.
- [x] CTX-005 / DOCS-001 repo-local: define injected Google-client boundary and implement resource listing domain path.
- [x] DOCS-002 repo-local: implement `ACTIVE_RESOURCE` and `SELECTION` context reads that return connector-verified normalized context.
- [x] DOCS-002 repo-local: reject oversized active-resource reads at the adapter boundary.
- [x] DOCS-003 repo-local: verify resource revision, target range/anchor, and original text hash before mutation.
- [x] DOCS-004 / ACTION-005 repo-local: implement MVP-safe replace/insert behavior only after target verification.
- [x] ACTION-004 repo-local: return conflict/no-mutation results for stale revisions, missing targets, and hash mismatch before provider mutation.
- [x] DOCS-001 / DOCS-004 repo-local: normalize validation, stale resource, target conflict, permission, rate-limit, and provider failure errors.
- [x] OPS-003 bootstrap: keep OAuth tokens, authorization headers, and document text out of this package's logging surface.
- [x] Repo hygiene: document tests and coverage commands, and ignore prompts, feedback, coverage output, dependencies, and build artifacts.
- [x] Repo layout: standardize the Python package under `src/ai_assist_google_docs_adapter/` and document `PYTHONPATH=src` unittest and compile checks.

## Completed M4 Read Path

- [x] M4-T3 / CTX-005: align fake-client resource-list results and read-context results with shared connector contract shapes.
- [x] M4-T3 / AUTH-003: verify injected token-provider handoff for resource listing and read-context operations.
- [x] M4-T3 / DOCS-001: verify fake-client resource discovery returns metadata-only connector resource refs.
- [x] M4-T3 / DOCS-002: verify fake-client `SELECTION` read context includes connector-verified provenance and range anchors.
- [x] M4-T3 / DOCS-002: verify fake-client `ACTIVE_RESOURCE` read context includes revision and content hash metadata.
- [x] M4-T3 / DOCS-001 / DOCS-002: verify permission, quota/rate-limit, timeout, reconnect-required, and oversized-context failures.

## Completed M7 Safe Apply

- [x] M7-T2 / DOCS-003 / DOCS-004: verify fake-client replace behavior gated by resource revision, target range, original-text hash, and resource identity validation.
- [x] M7-T2 / DOCS-004: verify fake-client insert behavior gated by resource revision and target anchor validation.
- [x] M7-T2 / ACTION-004: return metadata-only conflict results for stale revision, missing target, hash mismatch, wrong resource, and unresolved provider indexes before mutation.
- [x] M7-T2 / DOCS-005: verify permission, timeout, quota/rate-limit, reconnect-required, and provider failure mappings for apply dependencies and mutation writes.
- [x] M7-T2 / OPS-003: verify apply token handoff and apply results exclude action payload plaintext, raw document text, replacement text, OAuth tokens, and authorization headers.

## Completed M8 Real Google Docs Read/Apply

- [x] M8-T3 / DOCS-001: verify real Google Drive/Docs resource discovery behind injected token-provider and Google-client boundaries with metadata-only resource results.
- [x] M8-T3 / DOCS-002: implement safe active-resource truncation metadata and verify selected-range reads retain revision and connector-verified anchors.
- [x] M8-T3 / DOCS-003 / DOCS-004: verify real target verification and safe replace/insert keep revision, range/anchor, original-text hash, resource ID, permission, and no-mutation conflict guarantees at the connector boundary.
- [x] M8-T3 / DOCS-005: verify Google permission, quota/rate-limit, timeout, revoked OAuth, stale revision, missing target, and uncertain mutation mappings use typed safe errors.
- [x] M8-T3 / OPS-003: verify read/apply results and adapter docs keep OAuth tokens, authorization headers, document text, selected text, replacement text, and action payload plaintext out of logs and metadata-only results.

## Completed M9 Trusted-User MVP Hardening

- [x] M9-T5 / DOCS-001: verify resource listing uses injected OAuth token access with the least-privilege Drive metadata scope.
- [x] M9-T5 / DOCS-002: verify read context preserves revision metadata, connector-verified provenance, truncation metadata, and typed safe errors.
- [x] M9-T5 / DOCS-003 / DOCS-004 / ACTION-004: verify safe apply keeps connector-verified revision, range/anchor, original-text hash, idempotency key, and no-mutation conflict behavior before writes.
- [x] M9-T5 / DOCS-005: normalize real-client Google permission, quota, timeout, revoked OAuth, stale revision, missing target, and uncertain mutation errors to safe adapter errors.

## Completed M9 Deployed Dev Gap Close

- [x] M9-T9.7 / DOCS-001: add a stdlib Google Drive/Docs HTTP client for deployed resource listing, document reads, and safe batchUpdate replace/insert calls behind the injected client boundary.
- [x] M9-T9.7 / DOCS-002: verify the real-client document read path extracts Google Docs text and preserves document ID, title, and revision metadata without logging document content.
- [x] M9-T9.7 / DOCS-004 / ACTION-004: add an orchestration-facing connector adapter for validate/apply handoff with connector-verified target metadata, idempotency-key propagation, no-mutation conflicts, and metadata-only apply results.

## Completed M10 Dogfood Runtime Handler

- [x] M10 dogfood / DOCS-001: expose package-level `http_app.handle_http_request` for `GET /resources` with safe auth validation, metadata-only resource results through the existing adapter boundary when injected/configured, no-store responses, and structured dependency/config errors when deployed Google token handoff is unavailable.
- [x] M10-T3 / DOCS-001 / AUTH-003: pass optional `googleAccountId` through the resource-list HTTP adapter into the token handoff request so the deployed dogfood runtime can select a connected Google account without exposing OAuth token material.
- [x] M10-T6 / DOCS-003 / DOCS-004 / ACTION-004: verify orchestration safe apply requires connector-verified target metadata, passes revision/range/hash/type/idempotency metadata into mutation requests, and returns metadata-only no-mutation results for stale, conflicting, unsupported, token, and permission failures.

## Milestone 11 Real User Isolation

- [ ] M11-T2: Add user A/user B token-handoff tests proving resource listing
  and read-context use only the authenticated user's Google OAuth token metadata
  and fail before Google calls for wrong-user or missing token handoffs.
- [ ] M11-T3: Ensure Google Docs `ACTIVE_RESOURCE` read and apply paths depend
  on persisted `ContextConsentGrants` loaded for the derived tenant/user and
  named resource, not static dogfood consent JSON.
- [ ] M11-T5: Add deterministic cross-user read/apply denial coverage proving
  user B cannot read, validate, or mutate user A's controlled Google Doc through
  user A's OAuth token, consent grant, proposed action, or session state.

## Pending Architecture Tasks

- [ ] REPO-001: decide final language/runtime, framework, package manager, package layout, migration cost, deployment target, and test strategy for this repo.
- [x] REPO-002: migrate the Google Docs adapter bootstrap to a Python package layout with equivalent behavior and tests before broad new feature work continues.
- [x] AUTH-003: integrate token access with the auth service boundary, including revoked/expired Google token reconnect-required errors.
- [x] AUTH-003: define exact least-privilege Google OAuth scopes for resource listing, context reads, and safe replace/insert.
- [x] DOCS-001 / DOCS-002: define first production adapter request/response shapes for authorized resource discovery and read-context handoff using injected clients.
- [x] CTX-005: align connector interface inputs/outputs with shared contracts for verify target and apply safe mutation.
- [x] DOCS-001: add real Google Drive/Docs resource discovery adapter using authorized OAuth tokens and metadata-only responses.
- [x] DOCS-001: add fake-client contract tests for permission, quota, revoked-token, timeout, and provider failure normalization.
- [ ] DOCS-001 / E2E-001: add integration tests for authorized resource discovery with auth-service token handoff and metadata-only results.
- [x] DOCS-002: add real Google Docs read-context adapter for `SELECTION` and `ACTIVE_RESOURCE` with revision metadata and no document-text logs.
- [ ] DOCS-002 / E2E-002: add integration tests for read-context handoff to context/orchestration using connector-verified normalized context.
- [x] DOCS-002: align oversized-content truncation or rejection with the context service policy once finalized.
- [x] DOCS-003: map Google-native revision/range/anchor semantics into connector-neutral verification results for orchestration.
- [x] DOCS-004: add real Google Docs safe replace/insert adapter with least-privilege scopes and updated revision metadata in successful results.
- [ ] DOCS-004 / E2E-004: add integration tests for safe apply-action with revision/range/hash validation, idempotent duplicate handling, and conflict results.
- [x] DOCS-005: document and implement bounded timeout/retry policy separately for read, verify, and mutate operations.
- [x] DOCS-005 / ACTION-006: add failure-mode validation for revoked OAuth, permission/quota errors, timeouts, stale documents, uncertain mutation results, and provider write failures.
- [x] ACTION-004: add internal service adapter and contract tests for idempotent apply-action handoff from orchestration.
- [x] ACTION-005: keep unsupported edit types rejected with typed unsupported-action errors.
- [x] OPS-003: add metadata-only logging adapter rules for future HTTP/internal adapters.
- [ ] OPS-004 / INFRA-004: add deployment pipeline checks for Google OAuth config, least-privilege scopes, metadata-only logs, metrics, and adapter dependency health.
- [ ] Quality: raise line coverage to at least 95% after real adapter boundaries are added.

## Future Production Tasks

- [ ] ACTION-005: add comment/suggestion action support only if product scope requires it.
- [ ] DOCS-003: add robust anchor recovery for changed documents after MVP conflict behavior is proven.
- [ ] DOCS-001: add Drive picker and file-level permission handling when the web/client flow is selected.
