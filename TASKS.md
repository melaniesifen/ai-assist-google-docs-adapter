# Task Breakdown

Update this file as implementation progresses. Check off completed tasks in the same change that implements them.

Canonical cross-repo tasks live in `../ai-assist-architecture/implementation-task-breakdown.md`. This repo owns the Google Docs adapter portions of `DOCS-*`, `CTX-005`, `ACTION-*`, `AUTH-003`, `OPS-003`, and `REPO-001` items, grounded by `../ai-assist-architecture/lld-context-connectors.md` and `../ai-assist-architecture/lld-actions-writeback.md`.

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

## Pending Architecture Tasks

- [ ] REPO-001: decide final language/runtime, framework, package manager, package layout, migration cost, deployment target, and test strategy for this repo.
- [ ] AUTH-003: integrate token access with the auth service boundary, including revoked/expired Google token reconnect-required errors.
- [ ] AUTH-003: define exact least-privilege Google OAuth scopes for resource listing, context reads, and safe replace/insert.
- [ ] CTX-005: align connector interface inputs/outputs with shared contracts for list resources, read context, verify target, and apply safe mutation.
- [ ] DOCS-001: add real Google Drive/Docs resource discovery adapter using authorized OAuth tokens and metadata-only responses.
- [ ] DOCS-001: add contract tests for permission, quota, revoked-token, timeout, and provider failure normalization.
- [ ] DOCS-001 / E2E-001: add integration tests for authorized resource discovery with auth-service token handoff and metadata-only results.
- [ ] DOCS-002: add real Google Docs read-context adapter for `SELECTION` and `ACTIVE_RESOURCE` with revision metadata and no document-text logs.
- [ ] DOCS-002 / E2E-002: add integration tests for read-context handoff to context/orchestration using connector-verified normalized context.
- [ ] DOCS-002: align oversized-content truncation or rejection with the context service policy once finalized.
- [ ] DOCS-003: map Google-native revision/range/anchor semantics into connector-neutral verification results for orchestration.
- [ ] DOCS-004: add real Google Docs safe replace/insert adapter with least-privilege scopes and updated revision metadata in successful results.
- [ ] DOCS-004 / E2E-004: add integration tests for safe apply-action with revision/range/hash validation, idempotent duplicate handling, and conflict results.
- [ ] DOCS-005: document and implement bounded timeout/retry policy separately for read, verify, and mutate operations.
- [ ] DOCS-005 / ACTION-006: add failure-mode validation for revoked OAuth, permission/quota errors, timeouts, stale documents, uncertain mutation results, and provider write failures.
- [ ] ACTION-004: add internal service adapter and contract tests for idempotent apply-action handoff from orchestration.
- [ ] ACTION-005: keep unsupported edit types rejected with typed unsupported-action errors.
- [ ] OPS-003: add metadata-only logging adapter rules for future HTTP/internal adapters.
- [ ] OPS-004 / INFRA-004: add deployment pipeline checks for Google OAuth config, least-privilege scopes, metadata-only logs, metrics, and adapter dependency health.
- [ ] Quality: raise line coverage to at least 95% after real adapter boundaries are added.

## Future Production Tasks

- [ ] ACTION-005: add comment/suggestion action support only if product scope requires it.
- [ ] DOCS-003: add robust anchor recovery for changed documents after MVP conflict behavior is proven.
- [ ] DOCS-001: add Drive picker and file-level permission handling when the web/client flow is selected.
