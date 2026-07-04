from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import parse_qs, urlparse

from .adapter import GoogleDocsAdapter
from .constants import GOOGLE_OAUTH_SCOPE_DRIVE_METADATA_READONLY
from .errors import GoogleDocsAdapterError
from .google_http_client import GoogleDriveDocsHttpClient


SERVICE_NAME = "ai-assist-google-docs-adapter"
ACCESS_TOKEN_ENV = "GOOGLE_DOCS_ACCESS_TOKEN"
TENANT_HEADER = "x-ai-assist-tenant-id"
USER_HEADER = "x-ai-assist-user-id"
_APP: GoogleDocsHttpApplication | None = None


def handle_http_request(
    *,
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
    query_string: str = "",
    body: bytes | None = None,
) -> dict[str, Any]:
    del body
    global _APP
    if _APP is None:
        _APP = create_app_from_env()
    parsed = urlparse(path)
    return _APP.handle(
        method=method.upper(),
        path=parsed.path,
        headers=headers or {},
        query=parse_qs(query_string or parsed.query),
    )


def create_app_from_env(env: dict[str, str] | None = None) -> "GoogleDocsHttpApplication":
    env = env or dict(os.environ)
    access_token = (env.get(ACCESS_TOKEN_ENV) or "").strip()
    adapter = None
    if access_token:
        adapter = GoogleDocsAdapter(
            google_client=GoogleDriveDocsHttpClient(),
            token_provider=_StaticAccessTokenProvider(access_token),
        )
    return GoogleDocsHttpApplication(adapter=adapter)


class GoogleDocsHttpApplication:
    def __init__(self, *, adapter: Any | None = None) -> None:
        self.adapter = adapter

    def handle(
        self,
        *,
        method: str,
        path: str,
        headers: dict[str, str],
        query: dict[str, list[str]],
    ) -> dict[str, Any]:
        try:
            _require_bearer(headers)
            if method == "GET" and path == "/resources":
                if self.adapter is None:
                    return _error_response(
                        503,
                        "GOOGLE_DOCS_RUNTIME_DEPENDENCY_UNAVAILABLE",
                        "Google Docs resource listing requires a deployed OAuth token handoff dependency.",
                        category="DEPENDENCY",
                        details={"dependency": "googleDocsTokenProvider"},
                    )
                identity = _identity_from_headers(headers)
                result = self.adapter.list_resources(
                    {
                        **identity,
                        "pageSize": _optional_int(_first(query, "pageSize")),
                        "pageToken": _first(query, "pageToken"),
                        "requestId": _header(headers, "x-request-id"),
                    }
                )
                return _json_response(200, result)

            return _error_response(
                404,
                "ROUTE_NOT_FOUND",
                "Route is not implemented by the Google Docs adapter.",
                category="VALIDATION",
            )
        except GoogleDocsAdapterError as error:
            return _error_response(
                error.http_status,
                error.code,
                error.message,
                category=_category_for_adapter_error(error),
                retryable=error.retryable,
                details=error.details,
            )
        except ValueError as error:
            return _error_response(400, "VALIDATION_ERROR", str(error), category="VALIDATION")


class _StaticAccessTokenProvider:
    def __init__(self, access_token: str) -> None:
        self.access_token = access_token

    def get_access_token(self, input_: dict[str, Any]) -> dict[str, Any]:
        return {
            "accessToken": self.access_token,
            "status": "active",
            "scopes": input_.get("requiredScopes") or [GOOGLE_OAUTH_SCOPE_DRIVE_METADATA_READONLY],
        }


def _identity_from_headers(headers: dict[str, str]) -> dict[str, str]:
    tenant_id = _header(headers, TENANT_HEADER)
    user_id = _header(headers, USER_HEADER)
    if not tenant_id or not user_id:
        raise ValueError(f"{TENANT_HEADER} and {USER_HEADER} headers are required.")
    return {"tenantId": tenant_id, "userId": user_id}


def _require_bearer(headers: dict[str, str]) -> str:
    authorization = _header(headers, "authorization") or ""
    if not authorization.startswith("Bearer ") or not authorization[len("Bearer ") :].strip():
        return _raise_authentication_required()
    return authorization[len("Bearer ") :].strip()


def _raise_authentication_required() -> str:
    raise GoogleDocsAdapterError(
        code="AUTHENTICATION_REQUIRED",
        message="Bearer product session token is required.",
        http_status=401,
    )


def _optional_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError as error:
        raise ValueError("pageSize must be an integer.") from error


def _first(query: dict[str, list[str]], name: str) -> str | None:
    values = query.get(name) or []
    return values[0] if values else None


def _header(headers: dict[str, str], name: str) -> str | None:
    lowered = name.lower()
    for key, value in headers.items():
        if str(key).lower() == lowered:
            return value
    return None


def _json_response(status: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": status,
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
        "body": json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"),
    }


def _error_response(
    status: int,
    code: str,
    message: str,
    *,
    category: str,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error = {
        "code": code,
        "category": category,
        "message": message,
        "retryable": retryable,
    }
    if details:
        error["details"] = details
    return _json_response(status, {"error": error, "service": SERVICE_NAME})


def _category_for_adapter_error(error: GoogleDocsAdapterError) -> str:
    if error.code == "AUTHENTICATION_REQUIRED" or error.http_status == 401:
        return "AUTHENTICATION"
    if error.http_status == 403:
        return "AUTHORIZATION"
    if error.http_status == 429:
        return "RATE_LIMITED"
    if error.http_status >= 500:
        return "DEPENDENCY"
    if error.http_status == 409:
        return "CONFLICT"
    return "VALIDATION"
