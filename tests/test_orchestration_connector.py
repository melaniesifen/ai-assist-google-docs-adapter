from ai_assist_google_docs_adapter import (
    GoogleDocsAdapter,
    GoogleDocsOrchestrationConnector,
    hash_content,
)
from common import IDENTITY, NOW, AdapterTestCase, adapter_with


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

    def test_apply_action_rejects_mismatched_verified_replace_target_before_provider_access(self):
        adapter, calls = adapter_with({"documentId": "doc-1", "revisionId": "rev-1", "text": "Hello world"})
        connector = GoogleDocsOrchestrationConnector(adapter)

        with self.assertRaises(ValueError) as raised:
            connector.apply_action(
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

        self.assertEqual(str(raised.exception), "verifiedTarget.targetRange does not match action")
        self.assertEqual(calls["tokens"], [])
        self.assertEqual(calls["mutations"], [])

    def test_apply_action_rejects_mismatched_verified_resource_revision_and_hash(self):
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
                with self.assertRaises(ValueError) as raised:
                    connector.apply_action(
                        {
                            "action": BASE_ACTION,
                            "verifiedTarget": verified_target,
                            "payload": {"proposedText": "there"},
                            "idempotencyKey": f"idem-mismatch-{field_name}",
                        }
                    )
                self.assertEqual(str(raised.exception), f"verifiedTarget.{field_name} does not match action")

        self.assertEqual(calls["tokens"], [])
        self.assertEqual(calls["mutations"], [])

    def test_apply_action_rejects_mismatched_verified_insert_anchor_before_provider_access(self):
        adapter, calls = adapter_with({"documentId": "doc-1", "revisionId": "rev-1", "text": "Hello world"})
        connector = GoogleDocsOrchestrationConnector(adapter)
        action = {
            **BASE_ACTION,
            "actionType": "insert_text",
            "targetRange": None,
            "targetAnchor": {"index": 5},
            "originalTextHash": None,
        }

        with self.assertRaises(ValueError) as raised:
            connector.apply_action(
                {
                    "action": action,
                    "verifiedTarget": {"resourceId": "doc-1", "resourceRevision": "rev-1", "targetAnchor": {"index": 6}},
                    "payload": {"insertText": ","},
                    "idempotencyKey": "idem-anchor-mismatch",
                }
            )

        self.assertEqual(str(raised.exception), "verifiedTarget.targetAnchor does not match action")
        self.assertEqual(calls["tokens"], [])
        self.assertEqual(calls["mutations"], [])

    def test_constructor_requires_google_docs_adapter(self):
        with self.assertRaises(TypeError):
            GoogleDocsOrchestrationConnector(object())
