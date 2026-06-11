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


class GoogleDocsAdapterWritebackTests(AdapterTestCase):
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

        self.assertEqual(result["status"], "CONFLICTED")
        self.assertEqual(result["conflictDetails"]["code"], ERROR_CODES["RESOURCE_STALE"])
        self.assertEqual(calls["mutations"], [])

    def test_apply_replace_insert_rejects_original_text_conflict_before_provider_mutation(self):
        adapter, calls = adapter_with({"revisionId": "rev-1", "text": "Hello friend"})

        result = adapter.apply_replace_insert(
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
        )

        self.assertEqual(result["status"], "CONFLICTED")
        self.assertEqual(result["conflictDetails"]["code"], ERROR_CODES["TARGET_CONFLICT"])
        self.assertEqual(result["conflictDetails"]["reason"], "ORIGINAL_TEXT_HASH_MISMATCH")
        self.assertEqual(calls["mutations"], [])

    def test_apply_replace_insert_rejects_wrong_resource_before_provider_mutation(self):
        adapter, calls = adapter_with({"documentId": "doc-2", "revisionId": "rev-1", "text": "Hello world"})

        result = adapter.apply_replace_insert(
            {
                **IDENTITY,
                "resourceId": "doc-1",
                "mutationType": MUTATION_TYPE_REPLACE_TEXT,
                "expectedRevision": "rev-1",
                "targetRange": {"startIndex": 6, "endIndex": 11},
                "originalTextHash": hash_content("world"),
                "text": "there",
                "idempotencyKey": "idem-wrong-resource",
            }
        )

        self.assertEqual(result["status"], "CONFLICTED")
        self.assertEqual(
            result["conflictDetails"],
            {
                "code": ERROR_CODES["TARGET_CONFLICT"],
                "reason": "RESOURCE_MISMATCH",
                "expectedResourceId": "doc-1",
                "currentResourceId": "doc-2",
            },
        )
        self.assertEqual(calls["mutations"], [])

    def test_apply_replace_insert_returns_conflict_for_missing_replace_target_before_provider_mutation(self):
        adapter, calls = adapter_with({"revisionId": "rev-1", "text": "Hello world"})

        result = adapter.apply_replace_insert(
            {
                **IDENTITY,
                "resourceId": "doc-1",
                "mutationType": MUTATION_TYPE_REPLACE_TEXT,
                "expectedRevision": "rev-1",
                "originalTextHash": hash_content("world"),
                "text": "there",
                "idempotencyKey": "idem-missing-range",
            }
        )

        self.assertEqual(result["status"], "CONFLICTED")
        self.assertEqual(
            result["conflictDetails"],
            {"code": ERROR_CODES["TARGET_CONFLICT"], "reason": "TARGET_RANGE_MISSING"},
        )
        self.assertEqual(calls["mutations"], [])

    def test_apply_replace_insert_returns_conflict_for_missing_original_hash_before_provider_mutation(self):
        adapter, calls = adapter_with({"revisionId": "rev-1", "text": "Hello world"})

        result = adapter.apply_replace_insert(
            {
                **IDENTITY,
                "resourceId": "doc-1",
                "mutationType": MUTATION_TYPE_REPLACE_TEXT,
                "expectedRevision": "rev-1",
                "targetRange": {"startIndex": 6, "endIndex": 11},
                "text": "there",
                "idempotencyKey": "idem-missing-hash",
            }
        )

        self.assertEqual(result["status"], "CONFLICTED")
        self.assertEqual(
            result["conflictDetails"],
            {"code": ERROR_CODES["TARGET_CONFLICT"], "reason": "ORIGINAL_TEXT_HASH_MISSING"},
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

    def test_apply_replace_insert_returns_conflict_for_missing_insert_anchor_before_provider_mutation(self):
        adapter, calls = adapter_with({"revisionId": "rev-1", "text": "Hello world"})

        result = adapter.apply_replace_insert(
            {
                **IDENTITY,
                "resourceId": "doc-1",
                "mutationType": MUTATION_TYPE_INSERT_TEXT,
                "expectedRevision": "rev-1",
                "text": "!",
                "idempotencyKey": "idem-missing-anchor",
            }
        )

        self.assertEqual(result["status"], "CONFLICTED")
        self.assertEqual(
            result["conflictDetails"],
            {"code": ERROR_CODES["TARGET_CONFLICT"], "reason": "TARGET_ANCHOR_MISSING"},
        )
        self.assertEqual(calls["mutations"], [])

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

        result = adapter.apply_replace_insert(
            {
                **IDENTITY,
                "resourceId": "doc-1",
                "mutationType": MUTATION_TYPE_INSERT_TEXT,
                "expectedRevision": "rev-1",
                "targetAnchor": {"index": 2},
                "text": "!",
                "idempotencyKey": "idem-bisect",
            }
        )

        self.assertEqual(result["status"], "CONFLICTED")
        self.assertEqual(result["conflictDetails"], {
            "code": ERROR_CODES["TARGET_CONFLICT"],
            "reason": "TARGET_ANCHOR_UNRESOLVED",
        })
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

    def test_apply_result_is_metadata_only_even_if_provider_returns_payload_text(self):
        class TokenProvider:
            def get_access_token(self, input_):
                return token_response(scopes=input_["requiredScopes"])

        class GoogleClient:
            def get_document(self, input_):
                return {"documentId": "doc-1", "revisionId": "rev-1", "text": "Hello world"}

            def apply_text_mutation(self, input_):
                return {
                    "providerOperationId": "op-plaintext",
                    "resourceRevision": "rev-2",
                    "text": input_["text"],
                    "documentText": "Hello there",
                    "replacementText": input_["text"],
                    "authorization": "Bearer token-1",
                }

        adapter = GoogleDocsAdapter(
            google_client=GoogleClient(),
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
                "idempotencyKey": "idem-metadata-only",
            }
        )

        self.assertEqual(
            result,
            {"status": "APPLIED", "providerOperationId": "op-plaintext", "resourceRevision": "rev-2"},
        )

