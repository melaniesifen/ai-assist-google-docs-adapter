import time
import unittest
from datetime import datetime, timezone

from ai_assist_google_docs_adapter import (
    CONTEXT_MODE_ACTIVE_RESOURCE,
    CONTEXT_MODE_SELECTION,
    ERROR_CODES,
    GOOGLE_OAUTH_SCOPE_DOCUMENTS,
    GOOGLE_OAUTH_SCOPE_DOCUMENTS_READONLY,
    GOOGLE_OAUTH_SCOPE_DRIVE_METADATA_READONLY,
    GoogleDocsAdapter,
    GoogleDocsAdapterError,
    MAX_ACTIVE_RESOURCE_BYTES,
    MUTATION_TYPE_INSERT_TEXT,
    MUTATION_TYPE_REPLACE_TEXT,
    hash_content,
    verify_mutation_target,
)


NOW = datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)
IDENTITY = {"tenantId": "tenant-1", "userId": "user-1"}
ALL_SCOPES = [
    GOOGLE_OAUTH_SCOPE_DRIVE_METADATA_READONLY,
    GOOGLE_OAUTH_SCOPE_DOCUMENTS_READONLY,
    GOOGLE_OAUTH_SCOPE_DOCUMENTS,
]


class StatusError(Exception):
    def __init__(self, status=None, code=None, name=None):
        super().__init__("provider error")
        self.status = status
        self.code = code
        if name is not None:
            self.name = name


class FakeDependencies:
    def __init__(self, document=None):
        self.document = document or {"revisionId": "rev-1", "text": "Hello world", "title": "Doc"}
        self.calls = {"tokens": [], "list": [], "get": [], "mutations": []}
        self.token_provider = self.TokenProvider(self.calls)
        self.google_client = self.GoogleClient(self.calls, self.document)

    class TokenProvider:
        def __init__(self, calls):
            self.calls = calls

        def get_access_token(self, input_):
            self.calls["tokens"].append(input_)
            return token_response(scopes=input_["requiredScopes"])

    class GoogleClient:
        def __init__(self, calls, document):
            self.calls = calls
            self.document = document

        def list_documents(self, input_):
            self.calls["list"].append(input_)
            return {
                "resources": [
                    {
                        "id": "doc-1",
                        "name": "Doc",
                        "modifiedTime": "2026-05-29T11:00:00.000Z",
                    }
                ],
                "nextPageToken": None,
            }

        def get_document(self, input_):
            self.calls["get"].append(input_)
            return self.document

        def apply_text_mutation(self, input_):
            self.calls["mutations"].append(input_)
            return {"providerOperationId": "op-1", "resourceRevision": "rev-2"}


def adapter_with(document=None):
    deps = FakeDependencies(document)
    return (
        GoogleDocsAdapter(
            google_client=deps.google_client,
            token_provider=deps.token_provider,
            clock=lambda: NOW,
        ),
        deps.calls,
    )


def token_response(*, scopes=ALL_SCOPES, status="active"):
    return {
        "accessToken": "token-1",
        "status": status,
        "scopes": scopes,
        "expiresAt": "2026-05-29T13:00:00.000Z",
        "googleAccountId": "google-user-1",
    }


def token_response_without_access_token(*, scopes=ALL_SCOPES, status="active"):
    return {
        "status": status,
        "scopes": scopes,
        "expiresAt": "2026-05-29T13:00:00.000Z",
        "googleAccountId": "google-user-1",
    }


class GoogleDocsAdapterTests(unittest.TestCase):
    def assert_adapter_error(self, callable_, *, code, http_status=None, retryable=None, details=None):
        with self.assertRaises(GoogleDocsAdapterError) as raised:
            callable_()
        error = raised.exception
        self.assertEqual(error.code, code)
        if http_status is not None:
            self.assertEqual(error.http_status, http_status)
        if retryable is not None:
            self.assertEqual(error.retryable, retryable)
        if details is not None:
            self.assertEqual(error.details, details)
        return error

    def test_list_resources_uses_injected_token_provider_and_google_client(self):
        adapter, calls = adapter_with()

        result = adapter.list_resources({**IDENTITY, "pageSize": 10, "requestId": "req-1"})

        self.assertEqual(calls["tokens"][0]["provider"], "google_docs")
        self.assertEqual(calls["tokens"][0]["purpose"], "google_docs_api")
        self.assertEqual(calls["tokens"][0]["operation"], "listResources")
        self.assertEqual(calls["tokens"][0]["requestId"], "req-1")
        self.assertEqual(calls["tokens"][0]["requiredScopes"], [GOOGLE_OAUTH_SCOPE_DRIVE_METADATA_READONLY])
        self.assertEqual(calls["list"][0]["accessToken"], "token-1")
        self.assertEqual(
            result,
            {
                "resources": [
                    {
                        "connector": "google_docs",
                        "provider": "google_docs",
                        "resourceType": "document",
                        "resourceId": "doc-1",
                        "displayName": "Doc",
                        "mimeType": "application/vnd.google-apps.document",
                        "modifiedTime": "2026-05-29T11:00:00.000Z",
                        "externalUrl": None,
                        "resourceRevision": None,
                        "revisionMetadata": {
                            "provider": "google_docs",
                            "modifiedTime": "2026-05-29T11:00:00.000Z",
                        },
                    }
                ],
                "nextPageToken": None,
            },
        )

    def test_read_context_returns_connector_verified_active_resource_context(self):
        adapter, _ = adapter_with({"revisionId": "rev-1", "text": "Alpha beta", "title": "Doc"})

        result = adapter.read_context(
            {
                **IDENTITY,
                "sessionId": "session-1",
                "resourceId": "doc-1",
                "contextMode": CONTEXT_MODE_ACTIVE_RESOURCE,
            }
        )
        context = result["context"]

        self.assertEqual(result["resourceRevision"], "rev-1")
        self.assertEqual(context["provider"], "google_docs")
        self.assertEqual(context["connector"], "google_docs")
        self.assertEqual(
            context["resourceRef"],
            {
                "connector": "google_docs",
                "provider": "google_docs",
                "resourceId": "doc-1",
                "resourceType": "document",
                "displayName": "Doc",
            },
        )
        self.assertEqual(context["resourceRevision"], "rev-1")
        self.assertEqual(context["revisionMetadata"], {"provider": "google_docs", "revisionId": "rev-1"})
        self.assertEqual(context["sourceType"], "connector_resource_excerpt")
        self.assertEqual(context["trustLevel"], "connector_verified")
        self.assertEqual(context["content"], "Alpha beta")
        self.assertEqual(context["contentHash"], hash_content("Alpha beta"))
        self.assertTrue(context["provenance"]["connectorVerified"])

    def test_read_context_returns_connector_verified_selected_text_and_target_range(self):
        adapter, _ = adapter_with({"revisionId": "rev-1", "text": "Alpha beta", "title": "Doc"})

        result = adapter.read_context(
            {
                **IDENTITY,
                "sessionId": "session-1",
                "resourceId": "doc-1",
                "contextMode": CONTEXT_MODE_SELECTION,
                "selectionRange": {"startIndex": 0, "endIndex": 5},
            }
        )
        context = result["context"]

        self.assertEqual(context["sourceType"], "connector_selection")
        self.assertEqual(context["content"], "Alpha")
        self.assertEqual(
            context["anchors"],
            {
                "selectionAnchor": {"range": {"startIndex": 0, "endIndex": 5}},
                "targetRange": {"startIndex": 0, "endIndex": 5},
            },
        )
        self.assertEqual(
            context["provenance"]["selectionAnchor"],
            {"range": {"startIndex": 0, "endIndex": 5}},
        )

    def test_read_context_token_handoff_includes_session_resource_mode_and_read_scope(self):
        adapter, calls = adapter_with({"revisionId": "rev-1", "text": "Alpha beta", "title": "Doc"})

        adapter.read_context(
            {
                **IDENTITY,
                "requestId": "req-read",
                "sessionId": "session-1",
                "resourceId": "doc-1",
                "contextMode": CONTEXT_MODE_SELECTION,
                "consentGrantId": "grant-1",
                "selectionRange": {"startIndex": 0, "endIndex": 5},
            }
        )

        self.assertEqual(
            calls["tokens"][0],
            {
                **IDENTITY,
                "provider": "google_docs",
                "purpose": "google_docs_api",
                "operation": "readContext",
                "requiredScopes": [GOOGLE_OAUTH_SCOPE_DOCUMENTS_READONLY],
                "requestId": "req-read",
                "sessionId": "session-1",
                "resourceId": "doc-1",
                "contextMode": CONTEXT_MODE_SELECTION,
                "consentGrantId": "grant-1",
                "resourceRef": {
                    "connector": "google_docs",
                    "provider": "google_docs",
                    "resourceId": "doc-1",
                    "resourceType": "document",
                },
            },
        )

    def test_token_handoff_never_receives_document_text_or_selection_payload(self):
        adapter, calls = adapter_with({"revisionId": "rev-1", "text": "Alpha beta", "title": "Doc"})

        adapter.read_context(
            {
                **IDENTITY,
                "sessionId": "session-1",
                "resourceId": "doc-1",
                "contextMode": CONTEXT_MODE_SELECTION,
                "selectionRange": {"startIndex": 0, "endIndex": 5},
            }
        )

        self.assertNotIn("selectionRange", calls["tokens"][0])
        self.assertNotIn("content", calls["tokens"][0])
        self.assertNotIn("text", calls["tokens"][0])

    def test_token_handoff_rejects_missing_required_google_scope_before_provider_call(self):
        class TokenProvider:
            def get_access_token(self, input_):
                return token_response(scopes=[])

        class GoogleClient:
            def get_document(self, input_):
                raise AssertionError("should not be called")

        adapter = GoogleDocsAdapter(
            google_client=GoogleClient(),
            token_provider=TokenProvider(),
            clock=lambda: NOW,
        )

        self.assert_adapter_error(
            lambda: adapter.read_context(
                {
                    **IDENTITY,
                    "sessionId": "session-1",
                    "resourceId": "doc-1",
                    "contextMode": CONTEXT_MODE_ACTIVE_RESOURCE,
                }
            ),
            code=ERROR_CODES["PERMISSION_DENIED"],
            http_status=403,
            details={
                "operation": "readContext",
                "missingScopes": [GOOGLE_OAUTH_SCOPE_DOCUMENTS_READONLY],
            },
        )

    def test_token_handoff_status_reconnect_states_do_not_require_access_token(self):
        for status in ("revoked", "expired", "reconnect_required"):
            with self.subTest(status=status):
                calls = {"get": []}

                class TokenProvider:
                    def get_access_token(self, input_):
                        return token_response_without_access_token(
                            scopes=input_["requiredScopes"],
                            status=status,
                        )

                class GoogleClient:
                    def get_document(self, input_):
                        calls["get"].append(input_)
                        raise AssertionError("should not be called")

                adapter = GoogleDocsAdapter(
                    google_client=GoogleClient(),
                    token_provider=TokenProvider(),
                    clock=lambda: NOW,
                )

                self.assert_adapter_error(
                    lambda: adapter.read_context(
                        {
                            **IDENTITY,
                            "sessionId": "session-1",
                            "resourceId": "doc-1",
                            "contextMode": CONTEXT_MODE_ACTIVE_RESOURCE,
                        }
                    ),
                    code=ERROR_CODES["TOKEN_RECONNECT_REQUIRED"],
                    http_status=401,
                )
                self.assertEqual(calls["get"], [])

    def test_token_handoff_rejects_missing_or_unknown_status_before_provider_call(self):
        for response in (
            {"accessToken": "token-1", "scopes": ALL_SCOPES},
            token_response(status="pending"),
        ):
            with self.subTest(response=response):
                calls = {"get": []}

                class TokenProvider:
                    def get_access_token(self, input_):
                        return response

                class GoogleClient:
                    def get_document(self, input_):
                        calls["get"].append(input_)
                        raise AssertionError("should not be called")

                adapter = GoogleDocsAdapter(
                    google_client=GoogleClient(),
                    token_provider=TokenProvider(),
                    clock=lambda: NOW,
                )

                self.assert_adapter_error(
                    lambda: adapter.read_context(
                        {
                            **IDENTITY,
                            "sessionId": "session-1",
                            "resourceId": "doc-1",
                            "contextMode": CONTEXT_MODE_ACTIVE_RESOURCE,
                        }
                    ),
                    code=ERROR_CODES["TOKEN_UNAVAILABLE"],
                    http_status=401,
                )
                self.assertEqual(calls["get"], [])

    def test_apply_replace_insert_requests_mutation_scope_from_token_boundary(self):
        adapter, calls = adapter_with({"revisionId": "rev-1", "text": "Hello world"})

        adapter.apply_replace_insert(
            {
                **IDENTITY,
                "resourceId": "doc-1",
                "mutationType": MUTATION_TYPE_REPLACE_TEXT,
                "expectedRevision": "rev-1",
                "targetRange": {"startIndex": 6, "endIndex": 11},
                "originalTextHash": hash_content("world"),
                "text": "there",
                "idempotencyKey": "idem-scope",
            }
        )

        self.assertEqual(calls["tokens"][0]["requiredScopes"], [GOOGLE_OAUTH_SCOPE_DOCUMENTS])

    def test_read_context_uses_google_docs_provider_indexes_for_non_bmp_selection(self):
        adapter, _ = adapter_with({"revisionId": "rev-1", "text": "A😀B", "title": "Doc"})

        result = adapter.read_context(
            {
                **IDENTITY,
                "sessionId": "session-1",
                "resourceId": "doc-1",
                "contextMode": CONTEXT_MODE_SELECTION,
                "selectionRange": {"startIndex": 1, "endIndex": 3},
            }
        )
        context = result["context"]

        self.assertEqual(context["content"], "😀")
        self.assertEqual(context["contentHash"], hash_content("😀"))
        self.assertEqual(context["anchors"]["targetRange"], {"startIndex": 1, "endIndex": 3})

    def test_read_context_rejects_selected_ranges_that_do_not_resolve(self):
        adapter, _ = adapter_with({"revisionId": "rev-1", "text": "Alpha beta", "title": "Doc"})

        self.assert_adapter_error(
            lambda: adapter.read_context(
                {
                    **IDENTITY,
                    "sessionId": "session-1",
                    "resourceId": "doc-1",
                    "contextMode": CONTEXT_MODE_SELECTION,
                    "selectionRange": {"startIndex": 6, "endIndex": 100},
                }
            ),
            code=ERROR_CODES["TARGET_CONFLICT"],
            http_status=409,
            details={"reason": "SELECTION_RANGE_UNRESOLVED"},
        )

    def test_read_context_rejects_oversized_active_resource_context(self):
        adapter, _ = adapter_with(
            {"revisionId": "rev-1", "text": "a" * (MAX_ACTIVE_RESOURCE_BYTES + 1), "title": "Doc"}
        )

        self.assert_adapter_error(
            lambda: adapter.read_context(
                {
                    **IDENTITY,
                    "sessionId": "session-1",
                    "resourceId": "doc-1",
                    "contextMode": CONTEXT_MODE_ACTIVE_RESOURCE,
                }
            ),
            code=ERROR_CODES["CONTEXT_TOO_LARGE"],
            http_status=413,
        )

    def test_read_context_accepts_active_resource_context_at_the_byte_limit(self):
        adapter, _ = adapter_with({"revisionId": "rev-1", "text": "a" * MAX_ACTIVE_RESOURCE_BYTES, "title": "Doc"})

        result = adapter.read_context(
            {
                **IDENTITY,
                "sessionId": "session-1",
                "resourceId": "doc-1",
                "contextMode": CONTEXT_MODE_ACTIVE_RESOURCE,
            }
        )
        context = result["context"]

        self.assertEqual(len(context["content"]), MAX_ACTIVE_RESOURCE_BYTES)

    def test_verify_mutation_target_accepts_safe_replace(self):
        verification = verify_mutation_target(
            document={"revisionId": "rev-1", "text": "Hello world"},
            mutation_type=MUTATION_TYPE_REPLACE_TEXT,
            expected_revision="rev-1",
            target_range={"startIndex": 6, "endIndex": 11},
            original_text_hash=hash_content("world"),
        )

        self.assertEqual(verification["mutationType"], MUTATION_TYPE_REPLACE_TEXT)
        self.assertEqual(verification["currentText"], "world")
        self.assertEqual(verification["targetRange"], {"startIndex": 6, "endIndex": 11})

    def test_verify_mutation_target_uses_provider_indexes_for_text_after_non_bmp_character(self):
        verification = verify_mutation_target(
            document={"revisionId": "rev-1", "text": "A😀B"},
            mutation_type=MUTATION_TYPE_REPLACE_TEXT,
            expected_revision="rev-1",
            target_range={"startIndex": 3, "endIndex": 4},
            original_text_hash=hash_content("B"),
        )

        self.assertEqual(verification["currentText"], "B")
        self.assertEqual(verification["targetRange"], {"startIndex": 3, "endIndex": 4})

    def test_verify_mutation_target_rejects_provider_ranges_inside_non_bmp_character(self):
        self.assert_adapter_error(
            lambda: verify_mutation_target(
                document={"revisionId": "rev-1", "text": "A😀B"},
                mutation_type=MUTATION_TYPE_REPLACE_TEXT,
                expected_revision="rev-1",
                target_range={"startIndex": 1, "endIndex": 2},
                original_text_hash=hash_content(""),
            ),
            code=ERROR_CODES["TARGET_CONFLICT"],
            http_status=409,
            details={"reason": "TARGET_RANGE_UNRESOLVED"},
        )

    def test_apply_replace_insert_rejects_stale_target_before_provider_mutation(self):
        adapter, calls = adapter_with({"revisionId": "rev-2", "text": "Hello world"})

        self.assert_adapter_error(
            lambda: adapter.apply_replace_insert(
                {
                    **IDENTITY,
                    "resourceId": "doc-1",
                    "mutationType": MUTATION_TYPE_REPLACE_TEXT,
                    "expectedRevision": "rev-1",
                    "targetRange": {"startIndex": 6, "endIndex": 11},
                    "originalTextHash": hash_content("world"),
                    "text": "there",
                    "idempotencyKey": "idem-1",
                }
            ),
            code=ERROR_CODES["RESOURCE_STALE"],
            http_status=409,
        )
        self.assertEqual(calls["mutations"], [])

    def test_apply_replace_insert_rejects_original_text_conflict_before_provider_mutation(self):
        adapter, calls = adapter_with({"revisionId": "rev-1", "text": "Hello friend"})

        self.assert_adapter_error(
            lambda: adapter.apply_replace_insert(
                {
                    **IDENTITY,
                    "resourceId": "doc-1",
                    "mutationType": MUTATION_TYPE_REPLACE_TEXT,
                    "expectedRevision": "rev-1",
                    "targetRange": {"startIndex": 6, "endIndex": 12},
                    "originalTextHash": hash_content("world!"),
                    "text": "there",
                    "idempotencyKey": "idem-1",
                }
            ),
            code=ERROR_CODES["TARGET_CONFLICT"],
            http_status=409,
        )
        self.assertEqual(calls["mutations"], [])

    def test_apply_replace_insert_applies_safe_replace_once_with_normalized_request(self):
        adapter, calls = adapter_with({"revisionId": "rev-1", "text": "Hello world"})

        result = adapter.apply_replace_insert(
            {
                **IDENTITY,
                "resourceId": "doc-1",
                "mutationType": MUTATION_TYPE_REPLACE_TEXT,
                "expectedRevision": "rev-1",
                "targetRange": {"startIndex": 6, "endIndex": 11},
                "originalTextHash": hash_content("world"),
                "text": "there",
                "idempotencyKey": "idem-1",
            }
        )

        self.assertEqual(
            result,
            {"status": "APPLIED", "providerOperationId": "op-1", "resourceRevision": "rev-2"},
        )
        self.assertEqual(len(calls["mutations"]), 1)
        self.assertEqual(calls["mutations"][0]["documentId"], "doc-1")
        self.assertEqual(calls["mutations"][0]["targetRange"], {"startIndex": 6, "endIndex": 11})

    def test_apply_replace_insert_supports_safe_insert_at_verified_anchor(self):
        adapter, calls = adapter_with({"revisionId": "rev-1", "text": "Hello world"})

        adapter.apply_replace_insert(
            {
                **IDENTITY,
                "resourceId": "doc-1",
                "mutationType": MUTATION_TYPE_INSERT_TEXT,
                "expectedRevision": "rev-1",
                "targetAnchor": {"index": 5},
                "text": ",",
                "idempotencyKey": "idem-2",
            }
        )

        self.assertEqual(len(calls["mutations"]), 1)
        self.assertEqual(calls["mutations"][0]["targetAnchor"], {"index": 5})

    def test_apply_replace_insert_preserves_provider_anchor_after_non_bmp_character(self):
        adapter, calls = adapter_with({"revisionId": "rev-1", "text": "A😀B"})

        adapter.apply_replace_insert(
            {
                **IDENTITY,
                "resourceId": "doc-1",
                "mutationType": MUTATION_TYPE_INSERT_TEXT,
                "expectedRevision": "rev-1",
                "targetAnchor": {"index": 3},
                "text": "!",
                "idempotencyKey": "idem-non-bmp",
            }
        )

        self.assertEqual(calls["mutations"][0]["targetAnchor"], {"index": 3})

    def test_apply_replace_insert_rejects_insert_anchors_inside_non_bmp_character(self):
        adapter, calls = adapter_with({"revisionId": "rev-1", "text": "A😀B"})

        self.assert_adapter_error(
            lambda: adapter.apply_replace_insert(
                {
                    **IDENTITY,
                    "resourceId": "doc-1",
                    "mutationType": MUTATION_TYPE_INSERT_TEXT,
                    "expectedRevision": "rev-1",
                    "targetAnchor": {"index": 2},
                    "text": "!",
                    "idempotencyKey": "idem-bisect",
                }
            ),
            code=ERROR_CODES["TARGET_CONFLICT"],
            http_status=409,
            details={"reason": "TARGET_ANCHOR_UNRESOLVED"},
        )
        self.assertEqual(calls["mutations"], [])

    def test_apply_replace_insert_rejects_unsupported_mutation_types_before_provider_mutation(self):
        adapter, calls = adapter_with({"revisionId": "rev-1", "text": "Hello world"})

        self.assert_adapter_error(
            lambda: adapter.apply_replace_insert(
                {
                    **IDENTITY,
                    "resourceId": "doc-1",
                    "mutationType": "COMMENT_TEXT",
                    "expectedRevision": "rev-1",
                    "targetRange": {"startIndex": 0, "endIndex": 5},
                    "text": "comment",
                    "idempotencyKey": "idem-3",
                }
            ),
            code=ERROR_CODES["UNSUPPORTED_MUTATION"],
            http_status=422,
        )
        self.assertEqual(calls["tokens"], [])
        self.assertEqual(calls["get"], [])
        self.assertEqual(calls["mutations"], [])

    def test_verify_target_rejects_unsupported_mutation_types_before_provider_access(self):
        adapter, calls = adapter_with({"revisionId": "rev-1", "text": "Hello world"})

        self.assert_adapter_error(
            lambda: adapter.verify_target(
                {
                    **IDENTITY,
                    "resourceId": "doc-1",
                    "mutationType": "COMMENT_TEXT",
                    "expectedRevision": "rev-1",
                    "targetRange": {"startIndex": 0, "endIndex": 5},
                }
            ),
            code=ERROR_CODES["UNSUPPORTED_MUTATION"],
            http_status=422,
        )
        self.assertEqual(calls["tokens"], [])
        self.assertEqual(calls["get"], [])

    def test_google_provider_errors_are_normalized(self):
        class TokenProvider:
            def get_access_token(self, input_):
                return token_response(scopes=input_["requiredScopes"])

        class GoogleClient:
            def get_document(self, input_):
                raise StatusError(status=429)

        adapter = GoogleDocsAdapter(
            google_client=GoogleClient(),
            token_provider=TokenProvider(),
            clock=lambda: NOW,
        )

        self.assert_adapter_error(
            lambda: adapter.read_context(
                {
                    **IDENTITY,
                    "sessionId": "session-1",
                    "resourceId": "doc-1",
                    "contextMode": CONTEXT_MODE_ACTIVE_RESOURCE,
                }
            ),
            code=ERROR_CODES["RATE_LIMITED"],
            retryable=True,
        )

    def test_google_permission_errors_map_to_permission_denied(self):
        class TokenProvider:
            def get_access_token(self, input_):
                return token_response(scopes=input_["requiredScopes"])

        class GoogleClient:
            def get_document(self, input_):
                raise StatusError(status=403)

        adapter = GoogleDocsAdapter(
            google_client=GoogleClient(),
            token_provider=TokenProvider(),
            clock=lambda: NOW,
        )

        self.assert_adapter_error(
            lambda: adapter.read_context(
                {
                    **IDENTITY,
                    "sessionId": "session-1",
                    "resourceId": "doc-1",
                    "contextMode": CONTEXT_MODE_ACTIVE_RESOURCE,
                }
            ),
            code=ERROR_CODES["PERMISSION_DENIED"],
            http_status=403,
            retryable=False,
        )

    def test_read_operations_retry_retryable_provider_failures_within_limit(self):
        class TokenProvider:
            def get_access_token(self, input_):
                return token_response(scopes=input_["requiredScopes"])

        class GoogleClient:
            def __init__(self):
                self.list_calls = 0

            def list_documents(self, input_):
                self.list_calls += 1
                if self.list_calls == 1:
                    raise StatusError(status=503)
                return {"resources": [{"id": "doc-1", "name": "Doc"}]}

        google_client = GoogleClient()
        adapter = GoogleDocsAdapter(
            google_client=google_client,
            token_provider=TokenProvider(),
            clock=lambda: NOW,
            read_retry_limit=1,
        )

        result = adapter.list_resources({**IDENTITY})

        self.assertEqual(google_client.list_calls, 2)
        self.assertEqual(result["resources"][0]["resourceId"], "doc-1")
        self.assertEqual(result["resources"][0]["connector"], "google_docs")

    def test_list_resources_permission_errors_map_to_permission_denied(self):
        class TokenProvider:
            def get_access_token(self, input_):
                return token_response(scopes=input_["requiredScopes"])

        class GoogleClient:
            def list_documents(self, input_):
                raise StatusError(status=403)

        adapter = GoogleDocsAdapter(
            google_client=GoogleClient(),
            token_provider=TokenProvider(),
            clock=lambda: NOW,
        )

        self.assert_adapter_error(
            lambda: adapter.list_resources({**IDENTITY}),
            code=ERROR_CODES["PERMISSION_DENIED"],
            http_status=403,
            retryable=False,
        )

    def test_list_resources_rate_limits_remain_retryable_after_retry_limit(self):
        class TokenProvider:
            def get_access_token(self, input_):
                return token_response(scopes=input_["requiredScopes"])

        class GoogleClient:
            def __init__(self):
                self.list_calls = 0

            def list_documents(self, input_):
                self.list_calls += 1
                raise StatusError(status=429)

        google_client = GoogleClient()
        adapter = GoogleDocsAdapter(
            google_client=google_client,
            token_provider=TokenProvider(),
            clock=lambda: NOW,
            read_retry_limit=1,
        )

        self.assert_adapter_error(
            lambda: adapter.list_resources({**IDENTITY}),
            code=ERROR_CODES["RATE_LIMITED"],
            http_status=429,
            retryable=True,
        )
        self.assertEqual(google_client.list_calls, 2)

    def test_list_resources_timeouts_map_to_typed_retryable_dependency_errors(self):
        class TokenProvider:
            def get_access_token(self, input_):
                return token_response(scopes=input_["requiredScopes"])

        class GoogleClient:
            def list_documents(self, input_):
                time.sleep(0.05)
                return {"resources": []}

        adapter = GoogleDocsAdapter(
            google_client=GoogleClient(),
            token_provider=TokenProvider(),
            clock=lambda: NOW,
            operation_timeout_seconds=0.001,
            read_retry_limit=0,
        )

        self.assert_adapter_error(
            lambda: adapter.list_resources({**IDENTITY}),
            code=ERROR_CODES["PROVIDER_TIMEOUT"],
            http_status=504,
            retryable=True,
        )

    def test_list_resources_reconnect_required_skips_google_client(self):
        calls = {"list": []}

        class TokenProvider:
            def get_access_token(self, input_):
                return token_response_without_access_token(
                    scopes=input_["requiredScopes"],
                    status="reconnect_required",
                )

        class GoogleClient:
            def list_documents(self, input_):
                calls["list"].append(input_)
                raise AssertionError("should not be called")

        adapter = GoogleDocsAdapter(
            google_client=GoogleClient(),
            token_provider=TokenProvider(),
            clock=lambda: NOW,
        )

        self.assert_adapter_error(
            lambda: adapter.list_resources({**IDENTITY}),
            code=ERROR_CODES["TOKEN_RECONNECT_REQUIRED"],
            http_status=401,
        )
        self.assertEqual(calls["list"], [])

    def test_list_resources_missing_metadata_scope_skips_google_client(self):
        calls = {"list": []}

        class TokenProvider:
            def get_access_token(self, input_):
                return token_response(scopes=[])

        class GoogleClient:
            def list_documents(self, input_):
                calls["list"].append(input_)
                raise AssertionError("should not be called")

        adapter = GoogleDocsAdapter(
            google_client=GoogleClient(),
            token_provider=TokenProvider(),
            clock=lambda: NOW,
        )

        self.assert_adapter_error(
            lambda: adapter.list_resources({**IDENTITY}),
            code=ERROR_CODES["PERMISSION_DENIED"],
            http_status=403,
            details={
                "operation": "listResources",
                "missingScopes": [GOOGLE_OAUTH_SCOPE_DRIVE_METADATA_READONLY],
            },
        )
        self.assertEqual(calls["list"], [])

    def test_mutation_writes_are_not_blindly_retried_after_provider_failure(self):
        class TokenProvider:
            def get_access_token(self, input_):
                return token_response(scopes=input_["requiredScopes"])

        class GoogleClient:
            def __init__(self):
                self.mutation_calls = 0

            def get_document(self, input_):
                return {"revisionId": "rev-1", "text": "Hello world"}

            def apply_text_mutation(self, input_):
                self.mutation_calls += 1
                raise StatusError(status=503)

        google_client = GoogleClient()
        adapter = GoogleDocsAdapter(
            google_client=google_client,
            token_provider=TokenProvider(),
            clock=lambda: NOW,
            read_retry_limit=1,
        )

        self.assert_adapter_error(
            lambda: adapter.apply_replace_insert(
                {
                    **IDENTITY,
                    "resourceId": "doc-1",
                    "mutationType": MUTATION_TYPE_REPLACE_TEXT,
                    "expectedRevision": "rev-1",
                    "targetRange": {"startIndex": 6, "endIndex": 11},
                    "originalTextHash": hash_content("world"),
                    "text": "there",
                    "idempotencyKey": "idem-4",
                }
            ),
            code=ERROR_CODES["PROVIDER_UNAVAILABLE"],
            retryable=False,
        )
        self.assertEqual(google_client.mutation_calls, 1)

    def test_mutation_rate_limits_are_not_retryable_after_provider_write_attempt(self):
        class TokenProvider:
            def get_access_token(self, input_):
                return token_response(scopes=input_["requiredScopes"])

        class GoogleClient:
            def __init__(self):
                self.mutation_calls = 0

            def get_document(self, input_):
                return {"revisionId": "rev-1", "text": "Hello world"}

            def apply_text_mutation(self, input_):
                self.mutation_calls += 1
                raise StatusError(status=429)

        google_client = GoogleClient()
        adapter = GoogleDocsAdapter(
            google_client=google_client,
            token_provider=TokenProvider(),
            clock=lambda: NOW,
        )

        self.assert_adapter_error(
            lambda: adapter.apply_replace_insert(
                {
                    **IDENTITY,
                    "resourceId": "doc-1",
                    "mutationType": MUTATION_TYPE_REPLACE_TEXT,
                    "expectedRevision": "rev-1",
                    "targetRange": {"startIndex": 6, "endIndex": 11},
                    "originalTextHash": hash_content("world"),
                    "text": "there",
                    "idempotencyKey": "idem-rate-limited-write",
                }
            ),
            code=ERROR_CODES["RATE_LIMITED"],
            http_status=429,
            retryable=False,
        )
        self.assertEqual(google_client.mutation_calls, 1)

    def test_provider_timeouts_map_to_typed_retryable_dependency_errors(self):
        class TokenProvider:
            def get_access_token(self, input_):
                return token_response(scopes=input_["requiredScopes"])

        class GoogleClient:
            def get_document(self, input_):
                time.sleep(0.05)
                return {"revisionId": "late", "text": "late"}

        adapter = GoogleDocsAdapter(
            google_client=GoogleClient(),
            token_provider=TokenProvider(),
            clock=lambda: NOW,
            operation_timeout_seconds=0.001,
            read_retry_limit=0,
        )

        self.assert_adapter_error(
            lambda: adapter.read_context(
                {
                    **IDENTITY,
                    "sessionId": "session-1",
                    "resourceId": "doc-1",
                    "contextMode": CONTEXT_MODE_ACTIVE_RESOURCE,
                }
            ),
            code=ERROR_CODES["PROVIDER_TIMEOUT"],
            http_status=504,
            retryable=True,
        )

    def test_mutation_timeouts_are_typed_but_not_marked_retryable(self):
        class TokenProvider:
            def get_access_token(self, input_):
                return token_response(scopes=input_["requiredScopes"])

        class GoogleClient:
            def get_document(self, input_):
                return {"revisionId": "rev-1", "text": "Hello world"}

            def apply_text_mutation(self, input_):
                time.sleep(0.05)
                return {"providerOperationId": "late"}

        adapter = GoogleDocsAdapter(
            google_client=GoogleClient(),
            token_provider=TokenProvider(),
            clock=lambda: NOW,
            operation_timeout_seconds=0.001,
            read_retry_limit=0,
        )

        self.assert_adapter_error(
            lambda: adapter.apply_replace_insert(
                {
                    **IDENTITY,
                    "resourceId": "doc-1",
                    "mutationType": MUTATION_TYPE_REPLACE_TEXT,
                    "expectedRevision": "rev-1",
                    "targetRange": {"startIndex": 6, "endIndex": 11},
                    "originalTextHash": hash_content("world"),
                    "text": "there",
                    "idempotencyKey": "idem-5",
                }
            ),
            code=ERROR_CODES["PROVIDER_TIMEOUT"],
            http_status=504,
            retryable=False,
        )

    def test_provider_native_mutation_timeout_errors_are_not_marked_retryable(self):
        class TokenProvider:
            def get_access_token(self, input_):
                return token_response(scopes=input_["requiredScopes"])

        class GoogleClient:
            def __init__(self):
                self.mutation_calls = 0

            def get_document(self, input_):
                return {"revisionId": "rev-1", "text": "Hello world"}

            def apply_text_mutation(self, input_):
                self.mutation_calls += 1
                raise StatusError(name="AbortError")

        google_client = GoogleClient()
        adapter = GoogleDocsAdapter(
            google_client=google_client,
            token_provider=TokenProvider(),
            clock=lambda: NOW,
        )

        self.assert_adapter_error(
            lambda: adapter.apply_replace_insert(
                {
                    **IDENTITY,
                    "resourceId": "doc-1",
                    "mutationType": MUTATION_TYPE_REPLACE_TEXT,
                    "expectedRevision": "rev-1",
                    "targetRange": {"startIndex": 6, "endIndex": 11},
                    "originalTextHash": hash_content("world"),
                    "text": "there",
                    "idempotencyKey": "idem-6",
                }
            ),
            code=ERROR_CODES["PROVIDER_TIMEOUT"],
            http_status=504,
            retryable=False,
        )
        self.assertEqual(google_client.mutation_calls, 1)

    def test_revoked_token_provider_errors_map_to_reconnect_required_errors(self):
        class TokenProvider:
            def get_access_token(self, input_):
                raise StatusError(code="TOKEN_REVOKED")

        class GoogleClient:
            def get_document(self, input_):
                raise AssertionError("should not be called")

        adapter = GoogleDocsAdapter(
            google_client=GoogleClient(),
            token_provider=TokenProvider(),
            clock=lambda: NOW,
        )

        self.assert_adapter_error(
            lambda: adapter.read_context(
                {
                    **IDENTITY,
                    "sessionId": "session-1",
                    "resourceId": "doc-1",
                    "contextMode": CONTEXT_MODE_ACTIVE_RESOURCE,
                }
            ),
            code=ERROR_CODES["TOKEN_RECONNECT_REQUIRED"],
            http_status=401,
        )


if __name__ == "__main__":
    unittest.main()
