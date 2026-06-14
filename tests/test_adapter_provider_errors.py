import time

from ai_assist_google_docs_adapter import (
    CONTEXT_MODE_ACTIVE_RESOURCE,
    CONTEXT_MODE_SELECTION,
    ERROR_CODES,
    GOOGLE_OAUTH_SCOPE_DOCUMENTS,
    GOOGLE_OAUTH_SCOPE_DOCUMENTS_READONLY,
    GOOGLE_OAUTH_SCOPE_DRIVE_METADATA_READONLY,
    GoogleDocsAdapter,
    MAX_ACTIVE_RESOURCE_BYTES,
    MUTATION_TYPE_INSERT_TEXT,
    MUTATION_TYPE_REPLACE_TEXT,
    hash_content,
    verify_mutation_target,
)
from common import (
    ALL_SCOPES,
    IDENTITY,
    NOW,
    AdapterTestCase,
    ResponseStatusError,
    StatusError,
    adapter_with,
    token_response,
    token_response_without_access_token,
)


class GoogleDocsAdapterProviderErrorTests(AdapterTestCase):
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

    def test_real_client_reason_payloads_map_quota_to_rate_limited(self):
        class TokenProvider:
            def get_access_token(self, input_):
                return token_response(scopes=input_["requiredScopes"])

        class GoogleClient:
            def list_documents(self, input_):
                raise ResponseStatusError(
                    403,
                    content=b'{"error":{"status":"RESOURCE_EXHAUSTED","errors":[{"reason":"userRateLimitExceeded"}]}}',
                )

        adapter = GoogleDocsAdapter(
            google_client=GoogleClient(),
            token_provider=TokenProvider(),
            clock=lambda: NOW,
            read_retry_limit=0,
        )

        self.assert_adapter_error(
            lambda: adapter.list_resources({**IDENTITY}),
            code=ERROR_CODES["RATE_LIMITED"],
            http_status=429,
            retryable=True,
            details={"operation": "listResources"},
        )

    def test_real_client_revoked_oauth_maps_to_reconnect_required(self):
        class TokenProvider:
            def get_access_token(self, input_):
                return token_response(scopes=input_["requiredScopes"])

        class GoogleClient:
            def get_document(self, input_):
                raise ResponseStatusError(
                    401,
                    content=b'{"error":{"status":"UNAUTHENTICATED","errors":[{"reason":"authError"}]}}',
                )

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
            details={"operation": "readContext"},
        )

    def test_real_client_stale_revision_errors_return_no_mutation_conflict(self):
        class TokenProvider:
            def get_access_token(self, input_):
                return token_response(scopes=input_["requiredScopes"])

        class GoogleClient:
            def __init__(self):
                self.mutation_calls = 0

            def get_document(self, input_):
                raise StatusError(reason="failedPrecondition")

            def apply_text_mutation(self, input_):
                self.mutation_calls += 1

        google_client = GoogleClient()
        adapter = GoogleDocsAdapter(
            google_client=google_client,
            token_provider=TokenProvider(),
            clock=lambda: NOW,
        )

        result = adapter.apply_replace_insert(
            {
                **IDENTITY,
                "resourceId": "doc-1",
                "mutationType": MUTATION_TYPE_REPLACE_TEXT,
                "expectedRevision": "rev-1",
                "targetRange": {"startIndex": 6, "endIndex": 11},
                "originalTextHash": hash_content("world"),
                "text": "there",
                "idempotencyKey": "idem-real-stale-revision",
            }
        )

        self.assertEqual(
            result,
            {
                "status": "CONFLICTED",
                "conflictDetails": {
                    "code": ERROR_CODES["RESOURCE_STALE"],
                    "operation": "applyReplaceInsert.verify",
                    "reason": "RESOURCE_REVISION_MISMATCH",
                },
            },
        )
        self.assertEqual(google_client.mutation_calls, 0)

    def test_real_client_invalid_target_errors_return_no_mutation_conflict(self):
        class TokenProvider:
            def get_access_token(self, input_):
                return token_response(scopes=input_["requiredScopes"])

        class GoogleClient:
            def __init__(self):
                self.mutation_calls = 0

            def get_document(self, input_):
                raise StatusError(reason="invalidArgument")

            def apply_text_mutation(self, input_):
                self.mutation_calls += 1

        google_client = GoogleClient()
        adapter = GoogleDocsAdapter(
            google_client=google_client,
            token_provider=TokenProvider(),
            clock=lambda: NOW,
        )

        result = adapter.apply_replace_insert(
            {
                **IDENTITY,
                "resourceId": "doc-1",
                "mutationType": MUTATION_TYPE_REPLACE_TEXT,
                "expectedRevision": "rev-1",
                "targetRange": {"startIndex": 6, "endIndex": 11},
                "originalTextHash": hash_content("world"),
                "text": "there",
                "idempotencyKey": "idem-real-missing-target",
            }
        )

        self.assertEqual(result["status"], "CONFLICTED")
        self.assertEqual(result["conflictDetails"]["code"], ERROR_CODES["TARGET_CONFLICT"])
        self.assertEqual(result["conflictDetails"]["reason"], "TARGET_UNVERIFIABLE")
        self.assertEqual(google_client.mutation_calls, 0)
