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


class GoogleDocsAdapterTokenHandoffTests(AdapterTestCase):
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

    def test_full_documents_scope_satisfies_readonly_context_handoff(self):
        class TokenProvider:
            def get_access_token(self, input_):
                return token_response(scopes=[GOOGLE_OAUTH_SCOPE_DOCUMENTS])

        class GoogleClient:
            def get_document(self, input_):
                return {"documentId": input_["documentId"], "revisionId": "rev-1", "text": "Alpha beta"}

        adapter = GoogleDocsAdapter(
            google_client=GoogleClient(),
            token_provider=TokenProvider(),
            clock=lambda: NOW,
        )

        result = adapter.read_context(
            {
                **IDENTITY,
                "sessionId": "session-1",
                "resourceId": "doc-1",
                "contextMode": CONTEXT_MODE_ACTIVE_RESOURCE,
            }
        )

        self.assertEqual(result["context"]["content"], "Alpha beta")

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

    def test_apply_token_handoff_does_not_receive_action_payload_plaintext(self):
        adapter, calls = adapter_with({"documentId": "doc-1", "revisionId": "rev-1", "text": "Hello world"})

        adapter.apply_replace_insert(
            {
                **IDENTITY,
                "resourceId": "doc-1",
                "mutationType": MUTATION_TYPE_REPLACE_TEXT,
                "expectedRevision": "rev-1",
                "targetRange": {"startIndex": 6, "endIndex": 11},
                "originalTextHash": hash_content("world"),
                "text": "there",
                "idempotencyKey": "idem-token-privacy",
            }
        )

        self.assertNotIn("text", calls["tokens"][0])
        self.assertNotIn("targetRange", calls["tokens"][0])
        self.assertNotIn("originalTextHash", calls["tokens"][0])
        self.assertNotIn("actionPayload", calls["tokens"][0])

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
