import { DEFAULT_CONTEXT_TTL_MS, SOURCE_TYPES, TRUST_LEVELS } from "./constants.js";
import { ERROR_CODES, adapterError } from "./errors.js";
import { hashContent } from "./hash.js";
import { assertInteger, assertNonEmptyString, assertPlainObject } from "./validation.js";

export function normalizeResource(resource) {
  assertPlainObject(resource, "resource");
  const resourceId = resource.resourceId ?? resource.id;
  assertNonEmptyString(resourceId, "resource.resourceId");
  assertNonEmptyString(resource.name, "resource.name");

  return {
    provider: "google_docs",
    resourceType: "document",
    resourceId,
    name: resource.name,
    mimeType: resource.mimeType ?? "application/vnd.google-apps.document",
    modifiedTime: resource.modifiedTime ?? null,
    webUrl: resource.webViewLink ?? resource.webUrl ?? null
  };
}

export function documentRevision(document) {
  assertPlainObject(document, "document");
  const revision = document.revisionId ?? document.revision ?? document.version ?? document.modifiedTime;
  assertNonEmptyString(revision, "document.revisionId");
  return revision;
}

export function documentText(document) {
  assertPlainObject(document, "document");
  if (typeof document.text === "string") {
    return document.text;
  }

  const structuralContent = document.body?.content;
  if (!Array.isArray(structuralContent)) {
    return "";
  }

  return structuralContent
    .flatMap((block) => block.paragraph?.elements ?? [])
    .map((element) => element.textRun?.content ?? "")
    .join("");
}

export function normalizeReadContext(input, { now = new Date(), ttlMs = DEFAULT_CONTEXT_TTL_MS } = {}) {
  assertPlainObject(input, "input");
  assertNonEmptyString(input.tenantId, "input.tenantId");
  assertNonEmptyString(input.userId, "input.userId");
  assertNonEmptyString(input.sessionId, "input.sessionId");
  assertNonEmptyString(input.resourceId, "input.resourceId");
  assertNonEmptyString(input.contextMode, "input.contextMode");

  const text = input.content;
  assertNonEmptyString(text, "input.content");
  const capturedAt = now.toISOString();
  const revision = input.resourceRevision;
  const sourceType =
    input.contextMode === "SELECTION" ? SOURCE_TYPES.CONNECTOR_SELECTION : SOURCE_TYPES.CONNECTOR_RESOURCE_EXCERPT;

  return {
    contextId: input.contextId,
    tenantId: input.tenantId,
    userId: input.userId,
    sessionId: input.sessionId,
    provider: "google_docs",
    resourceRef: { provider: "google_docs", resourceId: input.resourceId },
    contextMode: input.contextMode,
    sourceType,
    trustLevel: TRUST_LEVELS.CONNECTOR_VERIFIED,
    content: text,
    contentHash: hashContent(text),
    anchors: input.anchors ?? {},
    resourceRevision: revision,
    metadata: input.metadata ?? {},
    provenance: {
      sourceType,
      trustLevel: TRUST_LEVELS.CONNECTOR_VERIFIED,
      connector: "google_docs",
      resourceId: input.resourceId,
      resourceVersion: revision,
      selectionAnchor: input.anchors?.selectionAnchor ?? null,
      capturedAt,
      clientSupplied: false,
      connectorVerified: true
    },
    capturedAt,
    expiresAt: new Date(now.getTime() + ttlMs).toISOString(),
    clientSupplied: false,
    connectorVerified: true
  };
}

export function normalizeRange(range, fieldName = "targetRange") {
  assertPlainObject(range, fieldName);
  assertInteger(range.startIndex, `${fieldName}.startIndex`);
  assertInteger(range.endIndex, `${fieldName}.endIndex`);
  if (range.startIndex < 0 || range.endIndex < range.startIndex) {
    throw adapterError(ERROR_CODES.VALIDATION_ERROR, `${fieldName} is invalid`, {
      details: { field: fieldName, range }
    });
  }
  return { startIndex: range.startIndex, endIndex: range.endIndex };
}

export function normalizeAnchor(anchor, fieldName = "targetAnchor") {
  assertPlainObject(anchor, fieldName);
  assertInteger(anchor.index, `${fieldName}.index`);
  if (anchor.index < 0) {
    throw adapterError(ERROR_CODES.VALIDATION_ERROR, `${fieldName}.index must be non-negative`, {
      details: { field: `${fieldName}.index` }
    });
  }
  return { index: anchor.index };
}

export function verifyReplaceTarget({ text, currentRevision, expectedRevision, targetRange, originalTextHash }) {
  assertRevision(currentRevision, expectedRevision);
  const range = normalizeRange(targetRange);
  assertNonEmptyString(originalTextHash, "originalTextHash");
  if (range.endIndex > text.length) {
    throw adapterError(ERROR_CODES.TARGET_CONFLICT, "target range no longer resolves", {
      httpStatus: 409,
      details: { reason: "TARGET_RANGE_UNRESOLVED" }
    });
  }

  const currentText = text.slice(range.startIndex, range.endIndex);
  const currentHash = hashContent(currentText);
  if (currentHash !== originalTextHash) {
    throw adapterError(ERROR_CODES.TARGET_CONFLICT, "target text hash does not match original text", {
      httpStatus: 409,
      details: { reason: "ORIGINAL_TEXT_HASH_MISMATCH", currentHash }
    });
  }

  return { targetRange: range, currentText, currentHash };
}

export function verifyInsertTarget({ text, currentRevision, expectedRevision, targetAnchor }) {
  assertRevision(currentRevision, expectedRevision);
  const anchor = normalizeAnchor(targetAnchor);
  if (anchor.index > text.length) {
    throw adapterError(ERROR_CODES.TARGET_CONFLICT, "insert target no longer resolves", {
      httpStatus: 409,
      details: { reason: "TARGET_ANCHOR_UNRESOLVED" }
    });
  }
  return { targetAnchor: anchor };
}

function assertRevision(currentRevision, expectedRevision) {
  assertNonEmptyString(currentRevision, "currentRevision");
  assertNonEmptyString(expectedRevision, "expectedRevision");
  if (currentRevision !== expectedRevision) {
    throw adapterError(ERROR_CODES.RESOURCE_STALE, "document revision is stale", {
      httpStatus: 409,
      details: { expectedRevision, currentRevision }
    });
  }
}
