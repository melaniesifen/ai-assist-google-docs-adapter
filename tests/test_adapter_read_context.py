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


class GoogleDocsAdapterReadContextTests(AdapterTestCase):
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

    def test_read_context_truncates_oversized_active_resource_context_safely(self):
        adapter, _ = adapter_with(
            {"revisionId": "rev-1", "text": "a" * (MAX_ACTIVE_RESOURCE_BYTES + 1), "title": "Doc"}
        )

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
        self.assertEqual(
            context["metadata"]["contentLimit"],
            {
                "maxBytes": MAX_ACTIVE_RESOURCE_BYTES,
                "originalBytes": MAX_ACTIVE_RESOURCE_BYTES + 1,
                "returnedBytes": MAX_ACTIVE_RESOURCE_BYTES,
                "truncated": True,
                "truncationReason": "MAX_ACTIVE_RESOURCE_BYTES",
            },
        )

    def test_read_context_accepts_active_resource_context_at_the_byte_limit(self):
        adapter, _ = adapter_with(
            {"revisionId": "rev-1", "text": "a" * MAX_ACTIVE_RESOURCE_BYTES, "title": "Doc"}
        )

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
        self.assertEqual(
            context["metadata"]["contentLimit"],
            {
                "maxBytes": MAX_ACTIVE_RESOURCE_BYTES,
                "originalBytes": MAX_ACTIVE_RESOURCE_BYTES,
                "returnedBytes": MAX_ACTIVE_RESOURCE_BYTES,
                "truncated": False,
            },
        )

    def test_read_context_truncates_without_splitting_utf8_characters(self):
        adapter, _ = adapter_with(
            {
                "revisionId": "rev-1",
                "text": "a" * (MAX_ACTIVE_RESOURCE_BYTES - 1) + "é",
                "title": "Doc",
            }
        )

        result = adapter.read_context(
            {
                **IDENTITY,
                "sessionId": "session-1",
                "resourceId": "doc-1",
                "contextMode": CONTEXT_MODE_ACTIVE_RESOURCE,
            }
        )
        context = result["context"]

        self.assertEqual(context["content"], "a" * (MAX_ACTIVE_RESOURCE_BYTES - 1))
        self.assertEqual(
            context["metadata"]["contentLimit"]["returnedBytes"],
            MAX_ACTIVE_RESOURCE_BYTES - 1,
        )
