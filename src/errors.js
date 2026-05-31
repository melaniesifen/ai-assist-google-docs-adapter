export class GoogleDocsAdapterError extends Error {
  constructor(code, message, { httpStatus = 400, details = {}, retryable = false } = {}) {
    super(message);
    this.name = "GoogleDocsAdapterError";
    this.code = code;
    this.httpStatus = httpStatus;
    this.details = details;
    this.retryable = retryable;
  }
}

export const ERROR_CODES = Object.freeze({
  VALIDATION_ERROR: "VALIDATION_ERROR",
  TOKEN_UNAVAILABLE: "TOKEN_UNAVAILABLE",
  TOKEN_RECONNECT_REQUIRED: "TOKEN_RECONNECT_REQUIRED",
  PERMISSION_DENIED: "PERMISSION_DENIED",
  RATE_LIMITED: "RATE_LIMITED",
  PROVIDER_TIMEOUT: "PROVIDER_TIMEOUT",
  PROVIDER_UNAVAILABLE: "PROVIDER_UNAVAILABLE",
  PROVIDER_ERROR: "PROVIDER_ERROR",
  RESOURCE_NOT_ACCESSIBLE: "RESOURCE_NOT_ACCESSIBLE",
  RESOURCE_STALE: "RESOURCE_STALE",
  TARGET_CONFLICT: "TARGET_CONFLICT",
  CONTEXT_TOO_LARGE: "CONTEXT_TOO_LARGE",
  UNSUPPORTED_MUTATION: "UNSUPPORTED_MUTATION"
});

export function adapterError(code, message, options) {
  return new GoogleDocsAdapterError(code, message, options);
}

export function isGoogleDocsAdapterError(error) {
  return error instanceof GoogleDocsAdapterError;
}

export function normalizeGoogleError(error, operation, { timeoutRetryable = true } = {}) {
  if (isGoogleDocsAdapterError(error)) {
    return error;
  }

  const status = error?.status ?? error?.code;
  if (status === "TOKEN_REVOKED" || status === "TOKEN_EXPIRED" || status === "RECONNECT_REQUIRED") {
    return adapterError(ERROR_CODES.TOKEN_RECONNECT_REQUIRED, "Google OAuth reconnect is required", {
      httpStatus: 401,
      details: { operation }
    });
  }
  if (status === "ETIMEDOUT" || status === "TIMEOUT" || error?.name === "AbortError") {
    return adapterError(ERROR_CODES.PROVIDER_TIMEOUT, "Google API request timed out", {
      httpStatus: 504,
      retryable: timeoutRetryable,
      details: { operation }
    });
  }
  if (status === 401 || status === 403) {
    return adapterError(ERROR_CODES.PERMISSION_DENIED, "Google authorization failed", {
      httpStatus: 403,
      details: { operation }
    });
  }
  if (status === 404) {
    return adapterError(ERROR_CODES.RESOURCE_NOT_ACCESSIBLE, "Google resource is not accessible", {
      httpStatus: 404,
      details: { operation }
    });
  }
  if (status === 429) {
    return adapterError(ERROR_CODES.RATE_LIMITED, "Google API rate limit exceeded", {
      httpStatus: 429,
      retryable: true,
      details: { operation }
    });
  }
  if (typeof status === "number" && status >= 500) {
    return adapterError(ERROR_CODES.PROVIDER_UNAVAILABLE, "Google API is unavailable", {
      httpStatus: 503,
      retryable: true,
      details: { operation }
    });
  }

  return adapterError(ERROR_CODES.PROVIDER_ERROR, "Google API request failed", {
    httpStatus: 502,
    retryable: false,
    details: { operation }
  });
}
