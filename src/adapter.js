import { randomUUID } from "node:crypto";
import {
  CONTEXT_MODES,
  DEFAULT_PAGE_SIZE,
  DEFAULT_OPERATION_TIMEOUT_MS,
  DEFAULT_READ_RETRY_LIMIT,
  MAX_ACTIVE_RESOURCE_BYTES,
  MAX_PAGE_SIZE,
  MUTATION_TYPES,
  PROVIDER
} from "./constants.js";
import { ERROR_CODES, adapterError, normalizeGoogleError } from "./errors.js";
import {
  documentRevision,
  documentText,
  normalizeReadContext,
  normalizeResource,
  normalizeRange,
  verifyInsertTarget,
  verifyReplaceTarget
} from "./document.js";
import { assertIdentity, assertNonEmptyString, assertPlainObject } from "./validation.js";

export class GoogleDocsAdapter {
  constructor({
    googleClient,
    tokenProvider,
    clock = () => new Date(),
    operationTimeoutMs = DEFAULT_OPERATION_TIMEOUT_MS,
    readRetryLimit = DEFAULT_READ_RETRY_LIMIT
  }) {
    if (!googleClient) {
      throw adapterError(ERROR_CODES.VALIDATION_ERROR, "googleClient is required");
    }
    if (!tokenProvider) {
      throw adapterError(ERROR_CODES.VALIDATION_ERROR, "tokenProvider is required");
    }
    this.googleClient = googleClient;
    this.tokenProvider = tokenProvider;
    this.clock = clock;
    this.operationTimeoutMs = normalizePositiveInteger(operationTimeoutMs, "operationTimeoutMs");
    this.readRetryLimit = normalizeNonNegativeInteger(readRetryLimit, "readRetryLimit");
  }

  async listResources(input) {
    assertIdentity(input);
    const accessToken = await this.#accessToken(input, "listResources");
    const pageSize = normalizePageSize(input.pageSize);

    try {
      const result = await this.#googleRead("listResources", () =>
        this.googleClient.listDocuments({
          accessToken,
          pageSize,
          pageToken: input.pageToken ?? null
        })
      );
      return {
        resources: (result.resources ?? result.files ?? []).map(normalizeResource),
        nextPageToken: result.nextPageToken ?? null
      };
    } catch (error) {
      throw normalizeGoogleError(error, "listResources");
    }
  }

  async readContext(input) {
    assertIdentity(input);
    assertNonEmptyString(input.sessionId, "input.sessionId");
    assertNonEmptyString(input.resourceId, "input.resourceId");
    assertNonEmptyString(input.contextMode, "input.contextMode");

    if (!Object.values(CONTEXT_MODES).includes(input.contextMode)) {
      throw adapterError(ERROR_CODES.VALIDATION_ERROR, "contextMode is not supported by Google Docs adapter", {
        details: { field: "contextMode", supportedModes: Object.values(CONTEXT_MODES) }
      });
    }

    const accessToken = await this.#accessToken(input, "readContext");
    try {
      const document = await this.#googleRead("readContext", () =>
        this.googleClient.getDocument({
          accessToken,
          documentId: input.resourceId
        })
      );
      const text = documentText(document);
      const revision = documentRevision(document);
      const selected =
        input.contextMode === CONTEXT_MODES.SELECTION ? selectedText(text, input.selectionRange) : null;
      const content =
        input.contextMode === CONTEXT_MODES.SELECTION ? selected.content : boundedActiveResourceText(text);
      const anchors =
        input.contextMode === CONTEXT_MODES.SELECTION
          ? { selectionAnchor: { range: selected.range }, targetRange: selected.range }
          : {};

      return normalizeReadContext(
        {
          contextId: input.contextId ?? randomUUID(),
          tenantId: input.tenantId,
          userId: input.userId,
          sessionId: input.sessionId,
          resourceId: input.resourceId,
          contextMode: input.contextMode,
          content,
          resourceRevision: revision,
          anchors,
          metadata: {
            documentTitle: document.title ?? null,
            contentBytes: Buffer.byteLength(content, "utf8")
          }
        },
        { now: this.clock() }
      );
    } catch (error) {
      throw normalizeGoogleError(error, "readContext");
    }
  }

  async verifyTarget(input) {
    assertIdentity(input);
    assertNonEmptyString(input.resourceId, "input.resourceId");
    assertNonEmptyString(input.expectedRevision, "input.expectedRevision");
    assertNonEmptyString(input.mutationType, "input.mutationType");
    assertSupportedMutationType(input.mutationType);

    const accessToken = await this.#accessToken(input, "verifyTarget");
    try {
      const document = await this.#googleRead("verifyTarget", () =>
        this.googleClient.getDocument({
          accessToken,
          documentId: input.resourceId
        })
      );
      return verifyMutationTarget({
        document,
        mutationType: input.mutationType,
        expectedRevision: input.expectedRevision,
        targetRange: input.targetRange,
        targetAnchor: input.targetAnchor,
        originalTextHash: input.originalTextHash
      });
    } catch (error) {
      throw normalizeGoogleError(error, "verifyTarget");
    }
  }

  async applyReplaceInsert(input) {
    assertIdentity(input);
    assertNonEmptyString(input.resourceId, "input.resourceId");
    assertNonEmptyString(input.expectedRevision, "input.expectedRevision");
    assertNonEmptyString(input.mutationType, "input.mutationType");
    assertSupportedMutationType(input.mutationType);
    assertNonEmptyString(input.text, "input.text");
    assertNonEmptyString(input.idempotencyKey, "input.idempotencyKey");

    const accessToken = await this.#accessToken(input, "applyReplaceInsert");
    try {
      const document = await this.#googleRead("applyReplaceInsert.verify", () =>
        this.googleClient.getDocument({
          accessToken,
          documentId: input.resourceId
        })
      );
      const verification = verifyMutationTarget({
        document,
        mutationType: input.mutationType,
        expectedRevision: input.expectedRevision,
        targetRange: input.targetRange,
        targetAnchor: input.targetAnchor,
        originalTextHash: input.originalTextHash
      });

      const mutationRequest = buildMutationRequest({
        accessToken,
        documentId: input.resourceId,
        mutationType: input.mutationType,
        text: input.text,
        idempotencyKey: input.idempotencyKey,
        verification
      });

      const providerResult = await this.#withOperationTimeout(
        "applyReplaceInsert.mutate",
        this.googleClient.applyTextMutation(mutationRequest),
        { retryable: false }
      );
      return {
        status: "APPLIED",
        providerOperationId: providerResult.providerOperationId ?? providerResult.operationId ?? null,
        resourceRevision: providerResult.resourceRevision ?? providerResult.revisionId ?? null
      };
    } catch (error) {
      throw normalizeGoogleError(error, "applyReplaceInsert", { timeoutRetryable: false });
    }
  }

  async #accessToken(input, operation) {
    try {
      const token = await this.#withOperationTimeout(
        `${operation}.token`,
        this.tokenProvider.getAccessToken({
          tenantId: input.tenantId,
          userId: input.userId,
          provider: PROVIDER,
          operation
        })
      );
      if (typeof token !== "string" || token.trim().length === 0) {
        throw adapterError(ERROR_CODES.TOKEN_UNAVAILABLE, "Google access token is unavailable", {
          httpStatus: 401,
          details: { operation }
        });
      }
      return token;
    } catch (error) {
      throw normalizeGoogleError(error, operation);
    }
  }

  async #googleRead(operation, call) {
    let attempt = 0;
    const maxAttempts = this.readRetryLimit + 1;

    while (attempt < maxAttempts) {
      attempt += 1;
      try {
        return await this.#withOperationTimeout(operation, call());
      } catch (error) {
        const normalized = normalizeGoogleError(error, operation);
        if (!normalized.retryable || attempt >= maxAttempts) {
          throw normalized;
        }
      }
    }

    throw adapterError(ERROR_CODES.PROVIDER_ERROR, "Google API request failed", {
      httpStatus: 502,
      details: { operation }
    });
  }

  async #withOperationTimeout(operation, promise, { retryable = true } = {}) {
    let timeoutId;
    const timeout = new Promise((_, reject) => {
      timeoutId = setTimeout(() => {
        reject(
          adapterError(ERROR_CODES.PROVIDER_TIMEOUT, "Google API request timed out", {
            httpStatus: 504,
            retryable,
            details: { operation, timeoutMs: this.operationTimeoutMs }
          })
        );
      }, this.operationTimeoutMs);
    });

    try {
      return await Promise.race([promise, timeout]);
    } finally {
      clearTimeout(timeoutId);
    }
  }
}

export function verifyMutationTarget({
  document,
  mutationType,
  expectedRevision,
  targetRange,
  targetAnchor,
  originalTextHash
}) {
  const text = documentText(document);
  const currentRevision = documentRevision(document);

  if (mutationType === MUTATION_TYPES.REPLACE_TEXT) {
    return {
      mutationType,
      resourceRevision: currentRevision,
      ...verifyReplaceTarget({ text, currentRevision, expectedRevision, targetRange, originalTextHash })
    };
  }

  if (mutationType === MUTATION_TYPES.INSERT_TEXT) {
    return {
      mutationType,
      resourceRevision: currentRevision,
      ...verifyInsertTarget({ text, currentRevision, expectedRevision, targetAnchor })
    };
  }

  throw adapterError(ERROR_CODES.UNSUPPORTED_MUTATION, "mutationType is not supported", {
    httpStatus: 422,
    details: { mutationType, supportedMutationTypes: Object.values(MUTATION_TYPES) }
  });
}

export function buildMutationRequest({ accessToken, documentId, mutationType, text, idempotencyKey, verification }) {
  assertPlainObject(verification, "verification");
  const base = {
    accessToken,
    documentId,
    mutationType,
    text,
    idempotencyKey,
    expectedRevision: verification.resourceRevision
  };

  if (mutationType === MUTATION_TYPES.REPLACE_TEXT) {
    return { ...base, targetRange: verification.targetRange };
  }

  if (mutationType === MUTATION_TYPES.INSERT_TEXT) {
    return { ...base, targetAnchor: verification.targetAnchor };
  }

  throw adapterError(ERROR_CODES.UNSUPPORTED_MUTATION, "mutationType is not supported", {
    httpStatus: 422,
    details: { mutationType }
  });
}

function normalizePageSize(pageSize) {
  if (pageSize === undefined || pageSize === null) {
    return DEFAULT_PAGE_SIZE;
  }
  if (!Number.isInteger(pageSize) || pageSize <= 0 || pageSize > MAX_PAGE_SIZE) {
    throw adapterError(ERROR_CODES.VALIDATION_ERROR, "pageSize is invalid", {
      details: { field: "pageSize", maxPageSize: MAX_PAGE_SIZE }
    });
  }
  return pageSize;
}

function normalizePositiveInteger(value, fieldName) {
  if (!Number.isInteger(value) || value <= 0) {
    throw adapterError(ERROR_CODES.VALIDATION_ERROR, `${fieldName} must be a positive integer`, {
      details: { field: fieldName }
    });
  }
  return value;
}

function normalizeNonNegativeInteger(value, fieldName) {
  if (!Number.isInteger(value) || value < 0) {
    throw adapterError(ERROR_CODES.VALIDATION_ERROR, `${fieldName} must be a non-negative integer`, {
      details: { field: fieldName }
    });
  }
  return value;
}

function assertSupportedMutationType(mutationType) {
  if (!Object.values(MUTATION_TYPES).includes(mutationType)) {
    throw adapterError(ERROR_CODES.UNSUPPORTED_MUTATION, "mutationType is not supported", {
      httpStatus: 422,
      details: { mutationType, supportedMutationTypes: Object.values(MUTATION_TYPES) }
    });
  }
}

function selectedText(text, range) {
  const normalizedRange = normalizeRange(range, "selectionRange");
  if (normalizedRange.endIndex > text.length) {
    throw adapterError(ERROR_CODES.TARGET_CONFLICT, "selection range no longer resolves", {
      httpStatus: 409,
      details: { reason: "SELECTION_RANGE_UNRESOLVED" }
    });
  }
  return {
    content: text.slice(normalizedRange.startIndex, normalizedRange.endIndex),
    range: normalizedRange
  };
}

function boundedActiveResourceText(text) {
  const contentBytes = Buffer.byteLength(text, "utf8");
  if (contentBytes > MAX_ACTIVE_RESOURCE_BYTES) {
    throw adapterError(ERROR_CODES.CONTEXT_TOO_LARGE, "active resource context exceeds maxBytes", {
      httpStatus: 413,
      details: { contentBytes, maxBytes: MAX_ACTIVE_RESOURCE_BYTES }
    });
  }
  return text;
}
