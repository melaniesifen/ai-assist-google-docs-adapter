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
    StatusError,
    adapter_with,
    token_response,
    token_response_without_access_token,
)


class GoogleDocsAdapterResourceTests(AdapterTestCase):
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

    def test_list_resources_token_handoff_includes_google_account_id(self):
        adapter, calls = adapter_with()

        adapter.list_resources(
            {
                **IDENTITY,
                "requestId": "req-1",
                "googleAccountId": "account-1",
            }
        )

        self.assertEqual(calls["tokens"][0]["operation"], "listResources")
        self.assertEqual(calls["tokens"][0]["googleAccountId"], "account-1")
        self.assertEqual(calls["tokens"][0]["requiredScopes"], [GOOGLE_OAUTH_SCOPE_DRIVE_METADATA_READONLY])

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
