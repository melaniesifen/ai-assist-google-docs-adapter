from ai_assist_google_docs_adapter import (
    ERROR_CODES,
    GoogleDocsAdapter,
    GoogleDocsOrchestrationConnector,
    hash_content,
)
from common import IDENTITY, NOW, AdapterTestCase, FakeDependencies, adapter_with, token_response


BASE_ACTION = {
    **IDENTITY,
    "actionId": "action-1",
    "sessionId": "session-1",
    "provider": "google_docs",
    "resourceId": "doc-1",
    "resourceRevision": "rev-1",
    "targetRange": {"startIndex": 6, "endIndex": 11},
    "originalTextHash": hash_content("world"),
    "actionType": "replace_text",
}


class GoogleDocsOrchestrationConnectorTests(AdapterTestCase):
    def test_validate_target_maps_action_to_verified_target(self):
        adapter, calls = adapter_with({"documentId": "doc-1", "revisionId": "rev-1", "text": "Hello world"})
        connector = GoogleDocsOrchestrationConnector(adapter)

        result = connector.validate_target(BASE_ACTION)

        self.assertEqual(result["valid"], True)
        self.assertEqual(
            result["verifiedTarget"],
            {
                "resourceId": "doc-1",
                "resourceRevision": "rev-1",
                "targetRange": {"startIndex": 6, "endIndex": 11},
                "originalTextHash": hash_content("world"),
            },
        )
        self.assertEqual(calls["tokens"][0]["operation"], "verifyTarget")

    def test_validate_target_returns_metadata_only_conflict(self):
        adapter, calls = adapter_with({"documentId": "doc-1", "revisionId": "rev-2", "text": "Hello world"})
        connector = GoogleDocsOrchestrationConnector(adapter)

        result = connector.validate_target(BASE_ACTION)

        self.assertEqual(result["valid"], False)
        self.assertEqual(result["reasonCode"], "RESOURCE_STALE")
        self.assertEqual(
            result["conflictDetails"],
            {
                "connectorCode": "RESOURCE_STALE",
                "reasonCode": "RESOURCE_STALE",
                "expectedRevision": "rev-1",
                "currentRevision": "rev-2",
            },
        )
        self.assertEqual(calls["mutations"], [])

    def test_apply_action_maps_payload_without_exposing_text_in_result(self):
        adapter, calls = adapter_with({"documentId": "doc-1", "revisionId": "rev-1", "text": "Hello world"})
        connector = GoogleDocsOrchestrationConnector(adapter)

        result = connector.apply_action(
            {
                "action": BASE_ACTION,
                "verifiedTarget": {
                    "resourceId": "doc-1",
                    "resourceRevision": "rev-1",
                    "targetRange": {"startIndex": 6, "endIndex": 11},
                    "originalTextHash": hash_content("world"),
                },
                "payload": {"proposedText": "there"},
                "idempotencyKey": "idem-1",
            }
        )

        self.assertEqual(result, {"providerOperationId": "op-1", "resourceRevision": "rev-2"})
        self.assertEqual(len(calls["mutations"]), 1)
        self.assertEqual(calls["mutations"][0]["text"], "there")
        self.assertEqual(calls["mutations"][0]["originalTextHash"], hash_content("world"))
        self.assertNotIn("text", result)

    def test_apply_action_supports_insert_payload(self):
        adapter, calls = adapter_with({"documentId": "doc-1", "revisionId": "rev-1", "text": "Hello world"})
        connector = GoogleDocsOrchestrationConnector(adapter)
        action = {
            **BASE_ACTION,
            "actionType": "insert_text",
            "targetRange": None,
            "targetAnchor": {"index": 5},
            "originalTextHash": None,
        }

        connector.apply_action(
            {
                "action": action,
                "verifiedTarget": {"resourceId": "doc-1", "resourceRevision": "rev-1", "targetAnchor": {"index": 5}},
                "payload": {"insertText": ","},
                "idempotencyKey": "idem-2",
            }
        )

        self.assertEqual(calls["mutations"][0]["mutationType"], "INSERT_TEXT")
        self.assertEqual(calls["mutations"][0]["targetAnchor"], {"index": 5})

    def test_apply_action_returns_no_mutation_conflict_for_mismatched_verified_replace_target(self):
        adapter, calls = adapter_with({"documentId": "doc-1", "revisionId": "rev-1", "text": "Hello world"})
        connector = GoogleDocsOrchestrationConnector(adapter)

        result = connector.apply_action(
            {
                "action": BASE_ACTION,
                "verifiedTarget": {
                    "resourceId": "doc-1",
                    "resourceRevision": "rev-1",
                    "targetRange": {"startIndex": 0, "endIndex": 5},
                    "originalTextHash": hash_content("Hello"),
                },
                "payload": {"proposedText": "there"},
                "idempotencyKey": "idem-mismatch",
            }
        )

        self.assertEqual(result["status"], "CONFLICTED")
        self.assertEqual(result["reasonCode"], "VERIFIED_TARGET_TARGET_RANGE_MISMATCH")
        self.assertEqual(result["conflictDetails"]["connectorCode"], ERROR_CODES["TARGET_CONFLICT"])
        self.assertEqual(calls["tokens"], [])
        self.assertEqual(calls["mutations"], [])

    def test_apply_action_returns_no_mutation_conflict_for_verified_resource_revision_and_hash_mismatch(self):
        adapter, calls = adapter_with({"documentId": "doc-1", "revisionId": "rev-1", "text": "Hello world"})
        connector = GoogleDocsOrchestrationConnector(adapter)

        for field_name, verified_target in (
            (
                "resourceId",
                {
                    "resourceId": "doc-2",
                    "resourceRevision": "rev-1",
                    "targetRange": {"startIndex": 6, "endIndex": 11},
                    "originalTextHash": hash_content("world"),
                },
            ),
            (
                "resourceRevision",
                {
                    "resourceId": "doc-1",
                    "resourceRevision": "rev-2",
                    "targetRange": {"startIndex": 6, "endIndex": 11},
                    "originalTextHash": hash_content("world"),
                },
            ),
            (
                "originalTextHash",
                {
                    "resourceId": "doc-1",
                    "resourceRevision": "rev-1",
                    "targetRange": {"startIndex": 6, "endIndex": 11},
                    "originalTextHash": hash_content("Hello"),
                },
            ),
        ):
            with self.subTest(field_name=field_name):
                result = connector.apply_action(
                    {
                        "action": BASE_ACTION,
                        "verifiedTarget": verified_target,
                        "payload": {"proposedText": "there"},
                        "idempotencyKey": f"idem-mismatch-{field_name}",
                    }
                )
                self.assertEqual(result["status"], "CONFLICTED")
                self.assertEqual(
                    result["reasonCode"],
                    f"VERIFIED_TARGET_{_expected_reason_field(field_name)}_MISMATCH",
                )

        self.assertEqual(calls["tokens"], [])
        self.assertEqual(calls["mutations"], [])

    def test_apply_action_returns_no_mutation_conflict_for_mismatched_verified_insert_anchor(self):
        adapter, calls = adapter_with({"documentId": "doc-1", "revisionId": "rev-1", "text": "Hello world"})
        connector = GoogleDocsOrchestrationConnector(adapter)
        action = {
            **BASE_ACTION,
            "actionType": "insert_text",
            "targetRange": None,
            "targetAnchor": {"index": 5},
            "originalTextHash": None,
        }

        result = connector.apply_action(
            {
                "action": action,
                "verifiedTarget": {"resourceId": "doc-1", "resourceRevision": "rev-1", "targetAnchor": {"index": 6}},
                "payload": {"insertText": ","},
                "idempotencyKey": "idem-anchor-mismatch",
            }
        )

        self.assertEqual(result["status"], "CONFLICTED")
        self.assertEqual(result["reasonCode"], "VERIFIED_TARGET_TARGET_ANCHOR_MISMATCH")
        self.assertEqual(calls["tokens"], [])
        self.assertEqual(calls["mutations"], [])

    def test_apply_action_returns_metadata_only_failed_result_for_unsupported_type(self):
        adapter, calls = adapter_with({"documentId": "doc-1", "revisionId": "rev-1", "text": "Hello world"})
        connector = GoogleDocsOrchestrationConnector(adapter)
        action = {**BASE_ACTION, "actionType": "delete_text"}

        result = connector.apply_action(
            {
                "action": action,
                "verifiedTarget": {
                    "resourceId": "doc-1",
                    "resourceRevision": "rev-1",
                    "targetRange": {"startIndex": 6, "endIndex": 11},
                    "originalTextHash": hash_content("world"),
                },
                "payload": {"proposedText": "there"},
                "idempotencyKey": "idem-unsupported",
            }
        )

        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["reasonCode"], ERROR_CODES["UNSUPPORTED_MUTATION"])
        self.assertEqual(result["conflictDetails"]["connectorCode"], ERROR_CODES["UNSUPPORTED_MUTATION"])
        self.assertEqual(calls["tokens"], [])
        self.assertEqual(calls["mutations"], [])

    def test_apply_action_returns_metadata_only_failed_result_for_token_reconnect(self):
        deps = _token_status_dependencies("revoked")
        adapter = GoogleDocsAdapter(
            google_client=deps.google_client,
            token_provider=deps.token_provider,
            clock=lambda: NOW,
        )
        connector = GoogleDocsOrchestrationConnector(adapter)

        result = connector.apply_action(
            {
                "action": BASE_ACTION,
                "verifiedTarget": {
                    "resourceId": "doc-1",
                    "resourceRevision": "rev-1",
                    "targetRange": {"startIndex": 6, "endIndex": 11},
                    "originalTextHash": hash_content("world"),
                },
                "payload": {"proposedText": "there"},
                "idempotencyKey": "idem-token",
            }
        )

        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["reasonCode"], ERROR_CODES["TOKEN_RECONNECT_REQUIRED"])
        self.assertEqual(result["conflictDetails"]["connectorCode"], ERROR_CODES["TOKEN_RECONNECT_REQUIRED"])
        self.assertEqual(deps.calls["mutations"], [])

    def test_validate_target_returns_metadata_only_permission_failure(self):
        deps = _missing_scope_dependencies()
        adapter = GoogleDocsAdapter(
            google_client=deps.google_client,
            token_provider=deps.token_provider,
            clock=lambda: NOW,
        )
        connector = GoogleDocsOrchestrationConnector(adapter)

        result = connector.validate_target(BASE_ACTION)

        self.assertEqual(result["valid"], False)
        self.assertEqual(result["reasonCode"], ERROR_CODES["PERMISSION_DENIED"])
        self.assertEqual(result["conflictDetails"]["connectorCode"], ERROR_CODES["PERMISSION_DENIED"])
        self.assertEqual(deps.calls["mutations"], [])

    def test_constructor_requires_google_docs_adapter(self):
        with self.assertRaises(TypeError):
            GoogleDocsOrchestrationConnector(object())


def _token_status_dependencies(status):
    deps = FakeDependencies({"documentId": "doc-1", "revisionId": "rev-1", "text": "Hello world"})

    class TokenProvider:
        def get_access_token(self, input_):
            deps.calls["tokens"].append(input_)
            return token_response(scopes=input_["requiredScopes"], status=status)

    deps.token_provider = TokenProvider()
    return deps


def _missing_scope_dependencies():
    deps = FakeDependencies({"documentId": "doc-1", "revisionId": "rev-1", "text": "Hello world"})

    class TokenProvider:
        def get_access_token(self, input_):
            deps.calls["tokens"].append(input_)
            return token_response(scopes=[])

    deps.token_provider = TokenProvider()
    return deps


def _expected_reason_field(field_name):
    return {
        "resourceId": "RESOURCE_ID",
        "resourceRevision": "RESOURCE_REVISION",
        "originalTextHash": "ORIGINAL_TEXT_HASH",
    }[field_name]
