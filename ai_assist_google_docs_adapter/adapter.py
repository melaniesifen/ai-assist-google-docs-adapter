from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from .constants import (
    CONTEXT_MODE_ACTIVE_RESOURCE,
    CONTEXT_MODE_SELECTION,
    CONTEXT_MODES,
    DEFAULT_OPERATION_TIMEOUT_SECONDS,
    DEFAULT_PAGE_SIZE,
    DEFAULT_READ_RETRY_LIMIT,
    MAX_ACTIVE_RESOURCE_BYTES,
    MAX_PAGE_SIZE,
    MUTATION_TYPE_INSERT_TEXT,
    MUTATION_TYPE_REPLACE_TEXT,
    MUTATION_TYPES,
    PROVIDER,
)
from .document import (
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
    CONTEXT_TOO_LARGE,
    PROVIDER_ERROR,
    PROVIDER_TIMEOUT,
    TARGET_CONFLICT,
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
        access_token = self._access_token(input_, "listResources")
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

        access_token = self._access_token(input_, "readContext")
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
            else:
                content = _bounded_active_resource_text(text)
                anchors = {}

            return normalize_read_context(
                {
                    "contextId": input_.get("contextId") or str(uuid4()),
                    "tenantId": input_["tenantId"],
                    "userId": input_["userId"],
                    "sessionId": input_["sessionId"],
                    "resourceId": input_["resourceId"],
                    "contextMode": input_["contextMode"],
                    "content": content,
                    "resourceRevision": revision,
                    "anchors": anchors,
                    "metadata": {
                        "documentTitle": document.get("title"),
                        "contentBytes": len(content.encode("utf-8")),
                    },
                },
                now=self.clock(),
            )
        except BaseException as error:
            raise normalize_google_error(error, "readContext") from error

    def verify_target(self, input_: dict[str, Any]) -> dict[str, Any]:
        assert_identity(input_)
        assert_non_empty_string(input_.get("resourceId"), "input.resourceId")
        assert_non_empty_string(input_.get("expectedRevision"), "input.expectedRevision")
        assert_non_empty_string(input_.get("mutationType"), "input.mutationType")
        assert_supported_mutation_type(input_["mutationType"])

        access_token = self._access_token(input_, "verifyTarget")
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

        access_token = self._access_token(input_, "applyReplaceInsert")
        try:
            document = self._google_read(
                "applyReplaceInsert.verify",
                lambda: _call_method(
                    self.google_client,
                    "get_document",
                    {"accessToken": access_token, "documentId": input_["resourceId"]},
                ),
            )
            verification = verify_mutation_target(
                document=document,
                mutation_type=input_["mutationType"],
                expected_revision=input_["expectedRevision"],
                target_range=input_.get("targetRange"),
                target_anchor=input_.get("targetAnchor"),
                original_text_hash=input_.get("originalTextHash"),
            )
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

    def _access_token(self, input_: dict[str, Any], operation: str) -> str:
        try:
            token = self._with_operation_timeout(
                f"{operation}.token",
                lambda: _call_method(
                    self.token_provider,
                    "get_access_token",
                    {
                        "tenantId": input_["tenantId"],
                        "userId": input_["userId"],
                        "provider": PROVIDER,
                        "operation": operation,
                    },
                ),
            )
            if not isinstance(token, str) or len(token.strip()) == 0:
                raise adapter_error(
                    TOKEN_UNAVAILABLE,
                    "Google access token is unavailable",
                    http_status=401,
                    details={"operation": operation},
                )
            return token
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
    target_range: dict[str, Any] | None = None,
    target_anchor: dict[str, Any] | None = None,
    original_text_hash: str | None = None,
) -> dict[str, Any]:
    text = document_text(document)
    current_revision = document_revision(document)

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


def _bounded_active_resource_text(text: str) -> str:
    content_bytes = len(text.encode("utf-8"))
    if content_bytes > MAX_ACTIVE_RESOURCE_BYTES:
        raise adapter_error(
            CONTEXT_TOO_LARGE,
            "active resource context exceeds maxBytes",
            http_status=413,
            details={"contentBytes": content_bytes, "maxBytes": MAX_ACTIVE_RESOURCE_BYTES},
        )
    return text


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
