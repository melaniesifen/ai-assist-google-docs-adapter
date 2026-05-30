import { ERROR_CODES, adapterError } from "./errors.js";

export function assertPlainObject(value, fieldName) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw adapterError(ERROR_CODES.VALIDATION_ERROR, `${fieldName} must be an object`, {
      details: { field: fieldName }
    });
  }
}

export function assertNonEmptyString(value, fieldName) {
  if (typeof value !== "string" || value.trim().length === 0) {
    throw adapterError(ERROR_CODES.VALIDATION_ERROR, `${fieldName} must be a non-empty string`, {
      details: { field: fieldName }
    });
  }
}

export function assertInteger(value, fieldName) {
  if (!Number.isInteger(value)) {
    throw adapterError(ERROR_CODES.VALIDATION_ERROR, `${fieldName} must be an integer`, {
      details: { field: fieldName }
    });
  }
}

export function assertIdentity(input) {
  assertPlainObject(input, "input");
  assertNonEmptyString(input.tenantId, "input.tenantId");
  assertNonEmptyString(input.userId, "input.userId");
}
