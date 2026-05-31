from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


VALIDATION_ERROR = "VALIDATION_ERROR"
TOKEN_UNAVAILABLE = "TOKEN_UNAVAILABLE"
TOKEN_RECONNECT_REQUIRED = "TOKEN_RECONNECT_REQUIRED"
PERMISSION_DENIED = "PERMISSION_DENIED"
RATE_LIMITED = "RATE_LIMITED"
PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
PROVIDER_ERROR = "PROVIDER_ERROR"
RESOURCE_NOT_ACCESSIBLE = "RESOURCE_NOT_ACCESSIBLE"
RESOURCE_STALE = "RESOURCE_STALE"
TARGET_CONFLICT = "TARGET_CONFLICT"
CONTEXT_TOO_LARGE = "CONTEXT_TOO_LARGE"
UNSUPPORTED_MUTATION = "UNSUPPORTED_MUTATION"

ERROR_CODES = {
    "VALIDATION_ERROR": VALIDATION_ERROR,
    "TOKEN_UNAVAILABLE": TOKEN_UNAVAILABLE,
    "TOKEN_RECONNECT_REQUIRED": TOKEN_RECONNECT_REQUIRED,
    "PERMISSION_DENIED": PERMISSION_DENIED,
    "RATE_LIMITED": RATE_LIMITED,
    "PROVIDER_TIMEOUT": PROVIDER_TIMEOUT,
    "PROVIDER_UNAVAILABLE": PROVIDER_UNAVAILABLE,
    "PROVIDER_ERROR": PROVIDER_ERROR,
    "RESOURCE_NOT_ACCESSIBLE": RESOURCE_NOT_ACCESSIBLE,
    "RESOURCE_STALE": RESOURCE_STALE,
    "TARGET_CONFLICT": TARGET_CONFLICT,
    "CONTEXT_TOO_LARGE": CONTEXT_TOO_LARGE,
    "UNSUPPORTED_MUTATION": UNSUPPORTED_MUTATION,
}


@dataclass
class GoogleDocsAdapterError(Exception):
    code: str
    message: str
    http_status: int = 400
    details: dict[str, Any] = field(default_factory=dict)
    retryable: bool = False

    def __post_init__(self) -> None:
        super().__init__(self.message)


def adapter_error(
    code: str,
    message: str,
    *,
    http_status: int = 400,
    details: dict[str, Any] | None = None,
    retryable: bool = False,
) -> GoogleDocsAdapterError:
    return GoogleDocsAdapterError(
        code=code,
        message=message,
        http_status=http_status,
        details=details or {},
        retryable=retryable,
    )


def normalize_google_error(
    error: BaseException,
    operation: str,
    *,
    timeout_retryable: bool = True,
    provider_retryable: bool = True,
) -> GoogleDocsAdapterError:
    if isinstance(error, GoogleDocsAdapterError):
        return error

    status = getattr(error, "status", None)
    if status is None:
        status = getattr(error, "code", None)
    name = getattr(error, "name", error.__class__.__name__)

    if status in {"TOKEN_REVOKED", "TOKEN_EXPIRED", "RECONNECT_REQUIRED"}:
        return adapter_error(
            TOKEN_RECONNECT_REQUIRED,
            "Google OAuth reconnect is required",
            http_status=401,
            details={"operation": operation},
        )
    if status in {"ETIMEDOUT", "TIMEOUT"} or name in {"AbortError", "TimeoutError"}:
        return adapter_error(
            PROVIDER_TIMEOUT,
            "Google API request timed out",
            http_status=504,
            retryable=timeout_retryable,
            details={"operation": operation},
        )
    if status in {401, 403}:
        return adapter_error(
            PERMISSION_DENIED,
            "Google authorization failed",
            http_status=403,
            details={"operation": operation},
        )
    if status == 404:
        return adapter_error(
            RESOURCE_NOT_ACCESSIBLE,
            "Google resource is not accessible",
            http_status=404,
            details={"operation": operation},
        )
    if status == 429:
        return adapter_error(
            RATE_LIMITED,
            "Google API rate limit exceeded",
            http_status=429,
            retryable=provider_retryable,
            details={"operation": operation},
        )
    if isinstance(status, int) and status >= 500:
        return adapter_error(
            PROVIDER_UNAVAILABLE,
            "Google API is unavailable",
            http_status=503,
            retryable=provider_retryable,
            details={"operation": operation},
        )

    return adapter_error(
        PROVIDER_ERROR,
        "Google API request failed",
        http_status=502,
        retryable=False,
        details={"operation": operation},
    )
