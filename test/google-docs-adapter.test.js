import test from "node:test";
import assert from "node:assert/strict";
import {
  CONTEXT_MODES,
  ERROR_CODES,
  GoogleDocsAdapter,
  MAX_ACTIVE_RESOURCE_BYTES,
  MUTATION_TYPES,
  hashContent,
  verifyMutationTarget
} from "../src/index.js";

const NOW = new Date("2026-05-29T12:00:00.000Z");

function fakeDependencies(document = { revisionId: "rev-1", text: "Hello world", title: "Doc" }) {
  const calls = { tokens: [], list: [], get: [], mutations: [] };
  return {
    calls,
    tokenProvider: {
      async getAccessToken(input) {
        calls.tokens.push(input);
        return "token-1";
      }
    },
    googleClient: {
      async listDocuments(input) {
        calls.list.push(input);
        return {
          resources: [{ id: "doc-1", name: "Doc", modifiedTime: "2026-05-29T11:00:00.000Z" }],
          nextPageToken: null
        };
      },
      async getDocument(input) {
        calls.get.push(input);
        return document;
      },
      async applyTextMutation(input) {
        calls.mutations.push(input);
        return { providerOperationId: "op-1", resourceRevision: "rev-2" };
      }
    }
  };
}

function adapterWith(document) {
  const deps = fakeDependencies(document);
  return { adapter: new GoogleDocsAdapter({ ...deps, clock: () => NOW }), calls: deps.calls };
}

const identity = {
  tenantId: "tenant-1",
  userId: "user-1"
};

test("listResources uses injected token provider and Google client", async () => {
  const { adapter, calls } = adapterWith();

  const result = await adapter.listResources({ ...identity, pageSize: 10 });

  assert.equal(calls.tokens[0].provider, "google_docs");
  assert.equal(calls.list[0].accessToken, "token-1");
  assert.deepEqual(result, {
    resources: [
      {
        provider: "google_docs",
        resourceType: "document",
        resourceId: "doc-1",
        name: "Doc",
        mimeType: "application/vnd.google-apps.document",
        modifiedTime: "2026-05-29T11:00:00.000Z",
        webUrl: null
      }
    ],
    nextPageToken: null
  });
});

test("readContext returns connector-verified normalized active resource context", async () => {
  const { adapter } = adapterWith({ revisionId: "rev-1", text: "Alpha beta", title: "Doc" });

  const context = await adapter.readContext({
    ...identity,
    sessionId: "session-1",
    resourceId: "doc-1",
    contextMode: CONTEXT_MODES.ACTIVE_RESOURCE
  });

  assert.equal(context.provider, "google_docs");
  assert.equal(context.resourceRevision, "rev-1");
  assert.equal(context.sourceType, "connector_resource_excerpt");
  assert.equal(context.trustLevel, "connector_verified");
  assert.equal(context.content, "Alpha beta");
  assert.equal(context.contentHash, hashContent("Alpha beta"));
  assert.equal(context.provenance.connectorVerified, true);
});

test("readContext returns connector-verified selected text and target range", async () => {
  const { adapter } = adapterWith({ revisionId: "rev-1", text: "Alpha beta", title: "Doc" });

  const context = await adapter.readContext({
    ...identity,
    sessionId: "session-1",
    resourceId: "doc-1",
    contextMode: CONTEXT_MODES.SELECTION,
    selectionRange: { startIndex: 0, endIndex: 5 }
  });

  assert.equal(context.sourceType, "connector_selection");
  assert.equal(context.content, "Alpha");
  assert.deepEqual(context.anchors.targetRange, { startIndex: 0, endIndex: 5 });
});

test("readContext rejects selected ranges that do not resolve against the current document", async () => {
  const { adapter } = adapterWith({ revisionId: "rev-1", text: "Alpha beta", title: "Doc" });

  await assert.rejects(
    () =>
      adapter.readContext({
        ...identity,
        sessionId: "session-1",
        resourceId: "doc-1",
        contextMode: CONTEXT_MODES.SELECTION,
        selectionRange: { startIndex: 6, endIndex: 100 }
      }),
    {
      code: ERROR_CODES.TARGET_CONFLICT,
      httpStatus: 409,
      details: { reason: "SELECTION_RANGE_UNRESOLVED" }
    }
  );
});

test("readContext rejects oversized active-resource context at the adapter boundary", async () => {
  const oversizedText = "a".repeat(MAX_ACTIVE_RESOURCE_BYTES + 1);
  const { adapter } = adapterWith({ revisionId: "rev-1", text: oversizedText, title: "Doc" });

  await assert.rejects(
    () =>
      adapter.readContext({
        ...identity,
        sessionId: "session-1",
        resourceId: "doc-1",
        contextMode: CONTEXT_MODES.ACTIVE_RESOURCE
      }),
    {
      code: ERROR_CODES.CONTEXT_TOO_LARGE,
      httpStatus: 413
    }
  );
});

test("readContext accepts active-resource context at the configured byte limit", async () => {
  const boundedText = "a".repeat(MAX_ACTIVE_RESOURCE_BYTES);
  const { adapter } = adapterWith({ revisionId: "rev-1", text: boundedText, title: "Doc" });

  const context = await adapter.readContext({
    ...identity,
    sessionId: "session-1",
    resourceId: "doc-1",
    contextMode: CONTEXT_MODES.ACTIVE_RESOURCE
  });

  assert.equal(context.content.length, MAX_ACTIVE_RESOURCE_BYTES);
});

test("verifyMutationTarget accepts safe replace with matching revision and original hash", () => {
  const verification = verifyMutationTarget({
    document: { revisionId: "rev-1", text: "Hello world" },
    mutationType: MUTATION_TYPES.REPLACE_TEXT,
    expectedRevision: "rev-1",
    targetRange: { startIndex: 6, endIndex: 11 },
    originalTextHash: hashContent("world")
  });

  assert.equal(verification.mutationType, MUTATION_TYPES.REPLACE_TEXT);
  assert.equal(verification.currentText, "world");
  assert.deepEqual(verification.targetRange, { startIndex: 6, endIndex: 11 });
});

test("applyReplaceInsert rejects stale target before provider mutation", async () => {
  const { adapter, calls } = adapterWith({ revisionId: "rev-2", text: "Hello world" });

  await assert.rejects(
    () =>
      adapter.applyReplaceInsert({
        ...identity,
        resourceId: "doc-1",
        mutationType: MUTATION_TYPES.REPLACE_TEXT,
        expectedRevision: "rev-1",
        targetRange: { startIndex: 6, endIndex: 11 },
        originalTextHash: hashContent("world"),
        text: "there",
        idempotencyKey: "idem-1"
      }),
    {
      code: ERROR_CODES.RESOURCE_STALE,
      httpStatus: 409
    }
  );
  assert.equal(calls.mutations.length, 0);
});

test("applyReplaceInsert rejects original text conflict before provider mutation", async () => {
  const { adapter, calls } = adapterWith({ revisionId: "rev-1", text: "Hello friend" });

  await assert.rejects(
    () =>
      adapter.applyReplaceInsert({
        ...identity,
        resourceId: "doc-1",
        mutationType: MUTATION_TYPES.REPLACE_TEXT,
        expectedRevision: "rev-1",
        targetRange: { startIndex: 6, endIndex: 12 },
        originalTextHash: hashContent("world!"),
        text: "there",
        idempotencyKey: "idem-1"
      }),
    {
      code: ERROR_CODES.TARGET_CONFLICT,
      httpStatus: 409
    }
  );
  assert.equal(calls.mutations.length, 0);
});

test("applyReplaceInsert applies safe replace once with normalized request", async () => {
  const { adapter, calls } = adapterWith({ revisionId: "rev-1", text: "Hello world" });

  const result = await adapter.applyReplaceInsert({
    ...identity,
    resourceId: "doc-1",
    mutationType: MUTATION_TYPES.REPLACE_TEXT,
    expectedRevision: "rev-1",
    targetRange: { startIndex: 6, endIndex: 11 },
    originalTextHash: hashContent("world"),
    text: "there",
    idempotencyKey: "idem-1"
  });

  assert.deepEqual(result, {
    status: "APPLIED",
    providerOperationId: "op-1",
    resourceRevision: "rev-2"
  });
  assert.equal(calls.mutations.length, 1);
  assert.equal(calls.mutations[0].documentId, "doc-1");
  assert.deepEqual(calls.mutations[0].targetRange, { startIndex: 6, endIndex: 11 });
});

test("applyReplaceInsert supports safe insert at verified anchor", async () => {
  const { adapter, calls } = adapterWith({ revisionId: "rev-1", text: "Hello world" });

  await adapter.applyReplaceInsert({
    ...identity,
    resourceId: "doc-1",
    mutationType: MUTATION_TYPES.INSERT_TEXT,
    expectedRevision: "rev-1",
    targetAnchor: { index: 5 },
    text: ",",
    idempotencyKey: "idem-2"
  });

  assert.equal(calls.mutations.length, 1);
  assert.deepEqual(calls.mutations[0].targetAnchor, { index: 5 });
});

test("applyReplaceInsert rejects unsupported mutation types before provider mutation", async () => {
  const { adapter, calls } = adapterWith({ revisionId: "rev-1", text: "Hello world" });

  await assert.rejects(
    () =>
      adapter.applyReplaceInsert({
        ...identity,
        resourceId: "doc-1",
        mutationType: "COMMENT_TEXT",
        expectedRevision: "rev-1",
        targetRange: { startIndex: 0, endIndex: 5 },
        text: "comment",
        idempotencyKey: "idem-3"
      }),
    {
      code: ERROR_CODES.UNSUPPORTED_MUTATION,
      httpStatus: 422
    }
  );
  assert.equal(calls.tokens.length, 0);
  assert.equal(calls.get.length, 0);
  assert.equal(calls.mutations.length, 0);
});

test("verifyTarget rejects unsupported mutation types before provider access", async () => {
  const { adapter, calls } = adapterWith({ revisionId: "rev-1", text: "Hello world" });

  await assert.rejects(
    () =>
      adapter.verifyTarget({
        ...identity,
        resourceId: "doc-1",
        mutationType: "COMMENT_TEXT",
        expectedRevision: "rev-1",
        targetRange: { startIndex: 0, endIndex: 5 }
      }),
    {
      code: ERROR_CODES.UNSUPPORTED_MUTATION,
      httpStatus: 422
    }
  );
  assert.equal(calls.tokens.length, 0);
  assert.equal(calls.get.length, 0);
});

test("Google provider errors are normalized", async () => {
  const calls = { tokens: [], get: [], mutations: [] };
  const adapter = new GoogleDocsAdapter({
    clock: () => NOW,
    tokenProvider: {
      async getAccessToken() {
        return "token-1";
      }
    },
    googleClient: {
      async getDocument() {
        const error = new Error("quota");
        error.status = 429;
        throw error;
      }
    }
  });

  await assert.rejects(
    () =>
      adapter.readContext({
        ...identity,
        sessionId: "session-1",
        resourceId: "doc-1",
        contextMode: CONTEXT_MODES.ACTIVE_RESOURCE
      }),
    {
      code: ERROR_CODES.RATE_LIMITED,
      retryable: true
    }
  );
  assert.deepEqual(calls.mutations, []);
});

test("read operations retry retryable provider failures within the configured limit", async () => {
  const calls = { list: 0 };
  const adapter = new GoogleDocsAdapter({
    clock: () => NOW,
    readRetryLimit: 1,
    tokenProvider: {
      async getAccessToken() {
        return "token-1";
      }
    },
    googleClient: {
      async listDocuments() {
        calls.list += 1;
        if (calls.list === 1) {
          const error = new Error("temporary");
          error.status = 503;
          throw error;
        }
        return { resources: [{ id: "doc-1", name: "Doc" }] };
      }
    }
  });

  const result = await adapter.listResources({ ...identity });

  assert.equal(calls.list, 2);
  assert.equal(result.resources[0].resourceId, "doc-1");
});

test("mutation writes are not blindly retried after provider failure", async () => {
  let mutationCalls = 0;
  const adapter = new GoogleDocsAdapter({
    clock: () => NOW,
    readRetryLimit: 1,
    tokenProvider: {
      async getAccessToken() {
        return "token-1";
      }
    },
    googleClient: {
      async getDocument() {
        return { revisionId: "rev-1", text: "Hello world" };
      },
      async applyTextMutation() {
        mutationCalls += 1;
        const error = new Error("write failed");
        error.status = 503;
        throw error;
      }
    }
  });

  await assert.rejects(
    () =>
      adapter.applyReplaceInsert({
        ...identity,
        resourceId: "doc-1",
        mutationType: MUTATION_TYPES.REPLACE_TEXT,
        expectedRevision: "rev-1",
        targetRange: { startIndex: 6, endIndex: 11 },
        originalTextHash: hashContent("world"),
        text: "there",
        idempotencyKey: "idem-4"
      }),
    {
      code: ERROR_CODES.PROVIDER_UNAVAILABLE,
      retryable: true
    }
  );
  assert.equal(mutationCalls, 1);
});

test("provider timeouts map to typed retryable dependency errors", async () => {
  const adapter = new GoogleDocsAdapter({
    clock: () => NOW,
    operationTimeoutMs: 5,
    readRetryLimit: 0,
    tokenProvider: {
      async getAccessToken() {
        return "token-1";
      }
    },
    googleClient: {
      async getDocument() {
        return new Promise(() => {});
      }
    }
  });

  await assert.rejects(
    () =>
      adapter.readContext({
        ...identity,
        sessionId: "session-1",
        resourceId: "doc-1",
        contextMode: CONTEXT_MODES.ACTIVE_RESOURCE
      }),
    {
      code: ERROR_CODES.PROVIDER_TIMEOUT,
      httpStatus: 504,
      retryable: true
    }
  );
});

test("mutation timeouts are typed but not marked retryable", async () => {
  const adapter = new GoogleDocsAdapter({
    clock: () => NOW,
    operationTimeoutMs: 5,
    readRetryLimit: 0,
    tokenProvider: {
      async getAccessToken() {
        return "token-1";
      }
    },
    googleClient: {
      async getDocument() {
        return { revisionId: "rev-1", text: "Hello world" };
      },
      async applyTextMutation() {
        return new Promise(() => {});
      }
    }
  });

  await assert.rejects(
    () =>
      adapter.applyReplaceInsert({
        ...identity,
        resourceId: "doc-1",
        mutationType: MUTATION_TYPES.REPLACE_TEXT,
        expectedRevision: "rev-1",
        targetRange: { startIndex: 6, endIndex: 11 },
        originalTextHash: hashContent("world"),
        text: "there",
        idempotencyKey: "idem-5"
      }),
    {
      code: ERROR_CODES.PROVIDER_TIMEOUT,
      httpStatus: 504,
      retryable: false
    }
  );
});

test("provider-native mutation timeout errors are not marked retryable", async () => {
  let mutationCalls = 0;
  const adapter = new GoogleDocsAdapter({
    clock: () => NOW,
    tokenProvider: {
      async getAccessToken() {
        return "token-1";
      }
    },
    googleClient: {
      async getDocument() {
        return { revisionId: "rev-1", text: "Hello world" };
      },
      async applyTextMutation() {
        mutationCalls += 1;
        const error = new Error("aborted");
        error.name = "AbortError";
        throw error;
      }
    }
  });

  await assert.rejects(
    () =>
      adapter.applyReplaceInsert({
        ...identity,
        resourceId: "doc-1",
        mutationType: MUTATION_TYPES.REPLACE_TEXT,
        expectedRevision: "rev-1",
        targetRange: { startIndex: 6, endIndex: 11 },
        originalTextHash: hashContent("world"),
        text: "there",
        idempotencyKey: "idem-6"
      }),
    {
      code: ERROR_CODES.PROVIDER_TIMEOUT,
      httpStatus: 504,
      retryable: false
    }
  );
  assert.equal(mutationCalls, 1);
});

test("revoked token provider errors map to reconnect-required errors", async () => {
  const adapter = new GoogleDocsAdapter({
    clock: () => NOW,
    tokenProvider: {
      async getAccessToken() {
        const error = new Error("revoked");
        error.code = "TOKEN_REVOKED";
        throw error;
      }
    },
    googleClient: {
      async getDocument() {
        throw new Error("should not be called");
      }
    }
  });

  await assert.rejects(
    () =>
      adapter.readContext({
        ...identity,
        sessionId: "session-1",
        resourceId: "doc-1",
        contextMode: CONTEXT_MODES.ACTIVE_RESOURCE
      }),
    {
      code: ERROR_CODES.TOKEN_RECONNECT_REQUIRED,
      httpStatus: 401
    }
  );
});
