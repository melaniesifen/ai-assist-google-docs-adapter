from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from .constants import (
    AUTH_TOKEN_PURPOSE,
    CONTEXT_MODE_ACTIVE_RESOURCE,
    CONTEXT_MODE_SELECTION,
    CONTEXT_MODES,
    DEFAULT_OPERATION_TIMEOUT_SECONDS,
    DEFAULT_PAGE_SIZE,
    DEFAULT_READ_RETRY_LIMIT,
    LIST_RESOURCES_REQUIRED_SCOPES,
    MAX_ACTIVE_RESOURCE_BYTES,
    MAX_PAGE_SIZE,
    MUTATE_DOCUMENT_REQUIRED_SCOPES,
    MUTATION_TYPE_INSERT_TEXT,
    MUTATION_TYPE_REPLACE_TEXT,
    MUTATION_TYPES,
    PROVIDER,
    READ_CONTEXT_REQUIRED_SCOPES,
)
from .document import (
    assert_document_resource,
    document_revision,
    document_text,
    normalize_read_context,
    normalize_range,
    normalize_resource,
    provider_indexed_slice,
    verify_insert_target,
    verify_replace_target,
)
from .errors import (
    PERMISSION_DENIED,
    PROVIDER_ERROR,
    PROVIDER_TIMEOUT,
    RESOURCE_STALE,
    TARGET_CONFLICT,
    TOKEN_RECONNECT_REQUIRED,
    TOKEN_UNAVAILABLE,
    UNSUPPORTED_MUTATION,
    VALIDATION_ERROR,
    adapter_error,
    normalize_google_error,
)
from .validation import assert_non_empty_string, assert_plain_object, assert_identity


class GoogleDocsAdapter:
    def __init__(
        self,
        *,
        google_client: Any,
        token_provider: Any,
        clock: Callable[[], datetime] | None = None,
        operation_timeout_seconds: float = DEFAULT_OPERATION_TIMEOUT_SECONDS,
        read_retry_limit: int = DEFAULT_READ_RETRY_LIMIT,
    ) -> None:
        if google_client is None:
            raise adapter_error(VALIDATION_ERROR, "googleClient is required")
        if token_provider is None:
            raise adapter_error(VALIDATION_ERROR, "tokenProvider is required")
        self.google_client = google_client
        self.token_provider = token_provider
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.operation_timeout_seconds = _normalize_positive_number(
            operation_timeout_seconds, "operationTimeoutSeconds"
        )
        self.read_retry_limit = _normalize_non_negative_integer(read_retry_limit, "readRetryLimit")

    def list_resources(self, input_: dict[str, Any]) -> dict[str, Any]:
        assert_identity(input_)
        access_token = self._access_token(
            input_,
            "listResources",
            required_scopes=LIST_RESOURCES_REQUIRED_SCOPES,
        )
        page_size = _normalize_page_size(input_.get("pageSize"))

        try:
            result = self._google_read(
                "listResources",
                lambda: _call_method(
                    self.google_client,
                    "list_documents",
                    {
                        "accessToken": access_token,
                        "pageSize": page_size,
                        "pageToken": input_.get("pageToken"),
                    },
                ),
            )
            resources = result.get("resources", result.get("files", []))
            return {
                "resources": [normalize_resource(resource) for resource in resources],
                "nextPageToken": result.get("nextPageToken"),
            }
        except BaseException as error:
            raise normalize_google_error(error, "listResources") from error

    def read_context(self, input_: dict[str, Any]) -> dict[str, Any]:
        assert_identity(input_)
        assert_non_empty_string(input_.get("sessionId"), "input.sessionId")
        assert_non_empty_string(input_.get("resourceId"), "input.resourceId")
        assert_non_empty_string(input_.get("contextMode"), "input.contextMode")
        if input_["contextMode"] not in CONTEXT_MODES:
            raise adapter_error(
                VALIDATION_ERROR,
                "contextMode is not supported by Google Docs adapter",
                details={"field": "contextMode", "supportedModes": list(CONTEXT_MODES)},
            )

        access_token = self._access_token(
            input_,
            "readContext",
            required_scopes=READ_CONTEXT_REQUIRED_SCOPES,
        )
        try:
            document = self._google_read(
                "readContext",
                lambda: _call_method(
                    self.google_client,
                    "get_document",
                    {"accessToken": access_token, "documentId": input_["resourceId"]},
                ),
            )
            text = document_text(document)
            revision = document_revision(document)
            if input_["contextMode"] == CONTEXT_MODE_SELECTION:
                selected = _selected_text(text, input_.get("selectionRange"))
                content = selected["content"]
                anchors = {
                    "selectionAnchor": {"range": selected["range"]},
                    "targetRange": selected["range"],
                }
                content_limit_metadata = {
                    "maxBytes": MAX_ACTIVE_RESOURCE_BYTES,
                    "originalBytes": len(content.encode("utf-8")),
                    "returnedBytes": len(content.encode("utf-8")),
                    "truncated": False,
                }
            else:
                bounded_content = _bounded_active_resource_text(text)
                content = bounded_content["content"]
                anchors = {}
                content_limit_metadata = bounded_content["metadata"]

            context = normalize_read_context(
                {
                    "contextId": input_.get("contextId") or str(uuid4()),
                    "tenantId": input_["tenantId"],
                    "userId": input_["userId"],
                    "sessionId": input_["sessionId"],
                    "resourceId": input_["resourceId"],
                    "resourceName": document.get("title"),
                    "contextMode": input_["contextMode"],
                    "content": content,
                    "resourceRevision": revision,
                    "anchors": anchors,
                    "metadata": {
                        "documentTitle": document.get("title"),
                        "contentBytes": len(content.encode("utf-8")),
                        "modifiedTime": document.get("modifiedTime"),
                        "contentLimit": content_limit_metadata,
                    },
                    "revisionMetadata": _read_revision_metadata(document, revision),
                },
                now=self.clock(),
            )
            return {
                "context": context,
                "resourceRevision": revision,
            }
        except BaseException as error:
            raise normalize_google_error(error, "readContext") from error

    def verify_target(self, input_: dict[str, Any]) -> dict[str, Any]:
        assert_identity(input_)
        assert_non_empty_string(input_.get("resourceId"), "input.resourceId")
        assert_non_empty_string(input_.get("expectedRevision"), "input.expectedRevision")
        assert_non_empty_string(input_.get("mutationType"), "input.mutationType")
        assert_supported_mutation_type(input_["mutationType"])

        access_token = self._access_token(
            input_,
            "verifyTarget",
            required_scopes=READ_CONTEXT_REQUIRED_SCOPES,
        )
        try:
            document = self._google_read(
                "verifyTarget",
                lambda: _call_method(
                    self.google_client,
                    "get_document",
                    {"accessToken": access_token, "documentId": input_["resourceId"]},
                ),
            )
            return verify_mutation_target(
                document=document,
                resource_id=input_["resourceId"],
                mutation_type=input_["mutationType"],
                expected_revision=input_["expectedRevision"],
                target_range=input_.get("targetRange"),
                target_anchor=input_.get("targetAnchor"),
                original_text_hash=input_.get("originalTextHash"),
            )
        except BaseException as error:
            raise normalize_google_error(error, "verifyTarget") from error

    def apply_replace_insert(self, input_: dict[str, Any]) -> dict[str, Any]:
        assert_identity(input_)
        assert_non_empty_string(input_.get("resourceId"), "input.resourceId")
        assert_non_empty_string(input_.get("expectedRevision"), "input.expectedRevision")
        assert_non_empty_string(input_.get("mutationType"), "input.mutationType")
        assert_supported_mutation_type(input_["mutationType"])
        assert_non_empty_string(input_.get("text"), "input.text")
        assert_non_empty_string(input_.get("idempotencyKey"), "input.idempotencyKey")

        access_token = self._access_token(
            input_,
            "applyReplaceInsert",
            required_scopes=MUTATE_DOCUMENT_REQUIRED_SCOPES,
        )
        try:
            try:
                document = self._google_read(
                    "applyReplaceInsert.verify",
                    lambda: _call_method(
                        self.google_client,
                        "get_document",
                        {"accessToken": access_token, "documentId": input_["resourceId"]},
                    ),
                )
            except BaseException as error:
                normalized = normalize_google_error(error, "applyReplaceInsert.verify")
                if normalized.code in {RESOURCE_STALE, TARGET_CONFLICT}:
                    return _conflict_result(normalized)
                raise normalized from error
            try:
                verification = verify_mutation_target(
                    document=document,
                    resource_id=input_["resourceId"],
                    mutation_type=input_["mutationType"],
                    expected_revision=input_["expectedRevision"],
                    target_range=input_.get("targetRange"),
                    target_anchor=input_.get("targetAnchor"),
                    original_text_hash=input_.get("originalTextHash"),
                )
            except BaseException as error:
                normalized = normalize_google_error(error, "applyReplaceInsert.verify")
                if normalized.code in {RESOURCE_STALE, TARGET_CONFLICT}:
                    return _conflict_result(normalized)
                raise normalized from error
            mutation_request = build_mutation_request(
                access_token=access_token,
                document_id=input_["resourceId"],
                mutation_type=input_["mutationType"],
                text=input_["text"],
                idempotency_key=input_["idempotencyKey"],
                verification=verification,
            )
            provider_result = self._with_operation_timeout(
                "applyReplaceInsert.mutate",
                lambda: _call_method(self.google_client, "apply_text_mutation", mutation_request),
                retryable=False,
            )
            return {
                "status": "APPLIED",
                "providerOperationId": provider_result.get("providerOperationId")
                or provider_result.get("operationId"),
                "resourceRevision": provider_result.get("resourceRevision")
                or provider_result.get("revisionId"),
            }
        except BaseException as error:
            raise normalize_google_error(
                error,
                "applyReplaceInsert",
                timeout_retryable=False,
                provider_retryable=False,
            ) from error

    def _access_token(
        self,
        input_: dict[str, Any],
        operation: str,
        *,
        required_scopes: tuple[str, ...],
    ) -> str:
        try:
            token_response = self._with_operation_timeout(
                f"{operation}.token",
                lambda: _call_method(
                    self.token_provider,
                    "get_access_token",
                    _token_handoff_request(input_, operation, required_scopes),
                ),
            )
            return _access_token_from_handoff(
                token_response,
                operation=operation,
                required_scopes=required_scopes,
            )
        except BaseException as error:
            raise normalize_google_error(error, operation) from error

    def _google_read(self, operation: str, call: Callable[[], Any]) -> Any:
        max_attempts = self.read_retry_limit + 1
        for attempt in range(1, max_attempts + 1):
            try:
                return self._with_operation_timeout(operation, call)
            except BaseException as error:
                normalized = normalize_google_error(error, operation)
                if not normalized.retryable or attempt >= max_attempts:
                    raise normalized from error
        raise adapter_error(
            PROVIDER_ERROR,
            "Google API request failed",
            http_status=502,
            details={"operation": operation},
        )

    def _with_operation_timeout(
        self,
        operation: str,
        call: Callable[[], Any],
        *,
        retryable: bool = True,
    ) -> Any:
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="google-docs-adapter")
        future = executor.submit(call)
        try:
            return future.result(timeout=self.operation_timeout_seconds)
        except FutureTimeoutError as error:
            future.cancel()
            raise adapter_error(
                PROVIDER_TIMEOUT,
                "Google API request timed out",
                http_status=504,
                retryable=retryable,
                details={
                    "operation": operation,
                    "timeoutSeconds": self.operation_timeout_seconds,
                },
            ) from error
        finally:
            executor.shutdown(wait=False, cancel_futures=True)


def verify_mutation_target(
    *,
    document: dict[str, Any],
    mutation_type: str,
    expected_revision: str,
    resource_id: str | None = None,
    target_range: dict[str, Any] | None = None,
    target_anchor: dict[str, Any] | None = None,
    original_text_hash: str | None = None,
) -> dict[str, Any]:
    text = document_text(document)
    current_revision = document_revision(document)
    if resource_id is not None:
        assert_document_resource(document, resource_id)

    if mutation_type == MUTATION_TYPE_REPLACE_TEXT:
        return {
            "mutationType": mutation_type,
            "resourceRevision": current_revision,
            **verify_replace_target(
                text=text,
                current_revision=current_revision,
                expected_revision=expected_revision,
                target_range=target_range,
                original_text_hash=original_text_hash,
            ),
        }
    if mutation_type == MUTATION_TYPE_INSERT_TEXT:
        return {
            "mutationType": mutation_type,
            "resourceRevision": current_revision,
            **verify_insert_target(
                text=text,
                current_revision=current_revision,
                expected_revision=expected_revision,
                target_anchor=target_anchor,
            ),
        }
    raise adapter_error(
        UNSUPPORTED_MUTATION,
        "mutationType is not supported",
        http_status=422,
        details={"mutationType": mutation_type, "supportedMutationTypes": list(MUTATION_TYPES)},
    )


def _conflict_result(error: Any) -> dict[str, Any]:
    return {
        "status": "CONFLICTED",
        "conflictDetails": {
            "code": error.code,
            **error.details,
        },
    }


def build_mutation_request(
    *,
    access_token: str,
    document_id: str,
    mutation_type: str,
    text: str,
    idempotency_key: str,
    verification: dict[str, Any],
) -> dict[str, Any]:
    assert_plain_object(verification, "verification")
    base = {
        "accessToken": access_token,
        "documentId": document_id,
        "mutationType": mutation_type,
        "text": text,
        "idempotencyKey": idempotency_key,
        "expectedRevision": verification.get("resourceRevision"),
    }
    if mutation_type == MUTATION_TYPE_REPLACE_TEXT:
        return {**base, "targetRange": verification.get("targetRange")}
    if mutation_type == MUTATION_TYPE_INSERT_TEXT:
        return {**base, "targetAnchor": verification.get("targetAnchor")}
    raise adapter_error(
        UNSUPPORTED_MUTATION,
        "mutationType is not supported",
        http_status=422,
        details={"mutationType": mutation_type},
    )


def assert_supported_mutation_type(mutation_type: str) -> None:
    if mutation_type not in MUTATION_TYPES:
        raise adapter_error(
            UNSUPPORTED_MUTATION,
            "mutationType is not supported",
            http_status=422,
            details={"mutationType": mutation_type, "supportedMutationTypes": list(MUTATION_TYPES)},
        )


def _token_handoff_request(
    input_: dict[str, Any],
    operation: str,
    required_scopes: tuple[str, ...],
) -> dict[str, Any]:
    request = {
        "tenantId": input_["tenantId"],
        "userId": input_["userId"],
        "provider": PROVIDER,
        "purpose": AUTH_TOKEN_PURPOSE,
        "operation": operation,
        "requiredScopes": list(required_scopes),
    }
    for field_name in (
        "requestId",
        "sessionId",
        "resourceId",
        "contextMode",
        "consentGrantId",
    ):
        if input_.get(field_name) is not None:
            request[field_name] = input_[field_name]
    if input_.get("resourceId") is not None:
        request["resourceRef"] = {
            "connector": PROVIDER,
            "provider": PROVIDER,
            "resourceId": input_["resourceId"],
            "resourceType": "document",
        }
    return request


def _access_token_from_handoff(
    token_response: Any,
    *,
    operation: str,
    required_scopes: tuple[str, ...],
) -> str:
    assert_plain_object(token_response, "tokenProvider.getAccessToken result")
    status = token_response.get("status")
    if status in {"revoked", "expired", "reconnect_required"}:
        raise adapter_error(
            TOKEN_RECONNECT_REQUIRED,
            "Google OAuth reconnect is required",
            http_status=401,
            details={"operation": operation},
        )
    if status != "active":
        raise adapter_error(
            TOKEN_UNAVAILABLE,
            "Google token status is not active",
            http_status=401,
            details={"operation": operation, "status": status},
        )

    access_token = token_response.get("accessToken")
    if not isinstance(access_token, str) or len(access_token.strip()) == 0:
        raise adapter_error(
            TOKEN_UNAVAILABLE,
            "Google access token is unavailable",
            http_status=401,
            details={"operation": operation},
        )

    granted_scopes = _normalize_granted_scopes(token_response.get("scopes"))
    missing_scopes = [scope for scope in required_scopes if scope not in granted_scopes]
    if missing_scopes:
        raise adapter_error(
            PERMISSION_DENIED,
            "Google OAuth token does not include required scopes",
            http_status=403,
            details={"operation": operation, "missingScopes": missing_scopes},
        )

    return access_token


def _normalize_granted_scopes(scopes: Any) -> set[str]:
    if isinstance(scopes, str):
        return {scope for scope in scopes.split() if scope}
    if isinstance(scopes, (list, tuple, set)):
        normalized = set()
        for scope in scopes:
            if not isinstance(scope, str) or len(scope.strip()) == 0:
                raise adapter_error(
                    VALIDATION_ERROR,
                    "token scopes must be non-empty strings",
                    details={"field": "scopes"},
                )
            normalized.add(scope)
        return normalized
    raise adapter_error(
        TOKEN_UNAVAILABLE,
        "Google token scopes are unavailable",
        http_status=401,
        details={"field": "scopes"},
    )


def _selected_text(text: str, range_: dict[str, Any]) -> dict[str, Any]:
    normalized_range = normalize_range(range_, "selectionRange")
    content = provider_indexed_slice(
        text,
        normalized_range["startIndex"],
        normalized_range["endIndex"],
        unresolved_reason="SELECTION_RANGE_UNRESOLVED",
    )
    return {
        "content": content,
        "range": normalized_range,
    }


def _bounded_active_resource_text(text: str) -> dict[str, Any]:
    content_bytes = len(text.encode("utf-8"))
    if content_bytes <= MAX_ACTIVE_RESOURCE_BYTES:
        return {
            "content": text,
            "metadata": {
                "maxBytes": MAX_ACTIVE_RESOURCE_BYTES,
                "originalBytes": content_bytes,
                "returnedBytes": content_bytes,
                "truncated": False,
            },
        }
    truncated = _truncate_utf8(text, MAX_ACTIVE_RESOURCE_BYTES)
    return {
        "content": truncated,
        "metadata": {
            "maxBytes": MAX_ACTIVE_RESOURCE_BYTES,
            "originalBytes": content_bytes,
            "returnedBytes": len(truncated.encode("utf-8")),
            "truncated": True,
            "truncationReason": "MAX_ACTIVE_RESOURCE_BYTES",
        },
    }


def _truncate_utf8(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _read_revision_metadata(document: dict[str, Any], revision: str) -> dict[str, Any]:
    metadata = {"provider": PROVIDER, "revisionId": revision}
    if document.get("modifiedTime") is not None:
        metadata["modifiedTime"] = document["modifiedTime"]
    return metadata


def _normalize_page_size(page_size: Any) -> int:
    if page_size is None:
        return DEFAULT_PAGE_SIZE
    if not isinstance(page_size, int) or isinstance(page_size, bool) or page_size <= 0 or page_size > MAX_PAGE_SIZE:
        raise adapter_error(
            VALIDATION_ERROR,
            "pageSize is invalid",
            details={"field": "pageSize", "maxPageSize": MAX_PAGE_SIZE},
        )
    return page_size


def _normalize_positive_number(value: Any, field_name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise adapter_error(
            VALIDATION_ERROR,
            f"{field_name} must be a positive number",
            details={"field": field_name},
        )
    return float(value)


def _normalize_non_negative_integer(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise adapter_error(
            VALIDATION_ERROR,
            f"{field_name} must be a non-negative integer",
            details={"field": field_name},
        )
    return value


def _call_method(target: Any, snake_name: str, input_: dict[str, Any]) -> Any:
    method = getattr(target, snake_name, None)
    if method is None:
        camel_name = _snake_to_camel(snake_name)
        method = getattr(target, camel_name)
    return method(input_)


def _snake_to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part[:1].upper() + part[1:] for part in rest)
