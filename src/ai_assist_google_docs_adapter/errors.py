from __future__ import annotations

from dataclasses import dataclass, field
import json
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

    status = _error_status(error)
    name = getattr(error, "name", error.__class__.__name__)
    reason = _error_reason(error)

    if status in {"TOKEN_REVOKED", "TOKEN_EXPIRED", "RECONNECT_REQUIRED"} or reason in {
        "invalid_grant",
        "authError",
        "invalidCredentials",
    }:
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
    if status == 401:
        return adapter_error(
            TOKEN_RECONNECT_REQUIRED,
            "Google OAuth reconnect is required",
            http_status=401,
            details={"operation": operation},
        )
    if status == 403 and reason in {
        "rateLimitExceeded",
        "userRateLimitExceeded",
        "quotaExceeded",
        "RESOURCE_EXHAUSTED",
    }:
        return adapter_error(
            RATE_LIMITED,
            "Google API rate limit exceeded",
            http_status=429,
            retryable=provider_retryable,
            details={"operation": operation},
        )
    if status == 403:
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
    if reason in {"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded", "RESOURCE_EXHAUSTED"}:
        return adapter_error(
            RATE_LIMITED,
            "Google API rate limit exceeded",
            http_status=429,
            retryable=provider_retryable,
            details={"operation": operation},
        )
    if reason in {"notFound", "NOT_FOUND"}:
        return adapter_error(
            RESOURCE_NOT_ACCESSIBLE,
            "Google resource is not accessible",
            http_status=404,
            details={"operation": operation},
        )
    if reason in {"forbidden", "insufficientPermissions", "PERMISSION_DENIED"}:
        return adapter_error(
            PERMISSION_DENIED,
            "Google authorization failed",
            http_status=403,
            details={"operation": operation},
        )
    if reason in {"failedPrecondition", "FAILED_PRECONDITION", "conditionNotMet"}:
        return adapter_error(
            RESOURCE_STALE,
            "Google resource revision is stale",
            http_status=409,
            details={"operation": operation, "reason": "RESOURCE_REVISION_MISMATCH"},
        )
    if reason in {"invalidArgument", "INVALID_ARGUMENT", "badRequest"} and _is_target_verification_operation(
        operation
    ):
        return adapter_error(
            TARGET_CONFLICT,
            "Google mutation target could not be verified",
            http_status=409,
            details={"operation": operation, "reason": "TARGET_UNVERIFIABLE"},
        )

    return adapter_error(
        PROVIDER_ERROR,
        "Google API request failed",
        http_status=502,
        retryable=False,
        details={"operation": operation},
    )


def _is_target_verification_operation(operation: str) -> bool:
    return operation in {"verifyTarget", "applyReplaceInsert.verify"}


def _error_status(error: BaseException) -> Any:
    status = getattr(error, "status", None)
    if status is None:
        status = getattr(error, "code", None)
    if status is None:
        response = getattr(error, "resp", None)
        status = getattr(response, "status", None)
    return status


def _error_reason(error: BaseException) -> str | None:
    for attr in ("reason", "error_reason"):
        value = getattr(error, attr, None)
        if isinstance(value, str) and value:
            return value

    details = getattr(error, "error_details", None)
    reason = _reason_from_details(details)
    if reason:
        return reason

    content = getattr(error, "content", None)
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="ignore")
    if isinstance(content, str) and content:
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return None
        return _reason_from_google_payload(parsed)
    return None


def _reason_from_google_payload(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    error = payload.get("error") if isinstance(payload.get("error"), dict) else payload
    for key in ("reason", "status"):
        value = error.get(key)
        if isinstance(value, str) and value:
            return value
    return _reason_from_details(error.get("errors") or error.get("details"))


def _reason_from_details(details: Any) -> str | None:
    if isinstance(details, dict):
        value = details.get("reason") or details.get("status")
        return value if isinstance(value, str) and value else None
    if isinstance(details, list):
        for item in details:
            value = _reason_from_details(item)
            if value:
                return value
    return None
