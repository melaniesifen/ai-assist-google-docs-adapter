import json
from io import BytesIO
from urllib.error import HTTPError

from ai_assist_google_docs_adapter import GoogleDriveDocsHttpClient, GoogleHttpClientError
from common import AdapterTestCase


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class GoogleDriveDocsHttpClientTests(AdapterTestCase):
    def test_list_documents_calls_drive_metadata_with_bearer_token(self):
        calls = []

        def transport(request, timeout):
            calls.append((request, timeout))
            return FakeResponse(
                {
                    "files": [
                        {
                            "id": "doc-1",
                            "name": "Doc",
                            "mimeType": "application/vnd.google-apps.document",
                            "modifiedTime": "2026-07-03T12:00:00.000Z",
                            "webViewLink": "https://docs.google.com/document/d/doc-1/edit",
                        }
                    ],
                    "nextPageToken": "next",
                }
            )

        client = GoogleDriveDocsHttpClient(transport=transport, timeout_seconds=3)
        result = client.list_documents({"accessToken": "token-1", "pageSize": 10, "pageToken": "page"})

        self.assertEqual(result["resources"][0]["id"], "doc-1")
        self.assertEqual(result["nextPageToken"], "next")
        self.assertIn("/drive/v3/files?", calls[0][0].full_url)
        self.assertEqual(calls[0][0].get_header("Authorization"), "Bearer token-1")
        self.assertEqual(calls[0][1], 3)

    def test_get_document_extracts_text_from_google_docs_structure(self):
        def transport(_request, _timeout):
            return FakeResponse(
                {
                    "documentId": "doc-1",
                    "title": "Doc",
                    "revisionId": "rev-1",
                    "body": {
                        "content": [
                            {
                                "paragraph": {
                                    "elements": [
                                        {"textRun": {"content": "Hello "}},
                                        {"textRun": {"content": "world\n"}},
                                    ]
                                }
                            }
                        ]
                    },
                }
            )

        client = GoogleDriveDocsHttpClient(transport=transport)
        result = client.get_document({"accessToken": "token-1", "documentId": "doc-1"})

        self.assertEqual(result["documentId"], "doc-1")
        self.assertEqual(result["revisionId"], "rev-1")
        self.assertEqual(result["text"], "Hello world\n")

    def test_apply_text_mutation_sends_required_revision_and_safe_requests(self):
        calls = []

        def transport(request, _timeout):
            calls.append(request)
            body = json.loads(request.data.decode("utf-8"))
            self.assertEqual(body["writeControl"], {"requiredRevisionId": "rev-1"})
            self.assertEqual(
                body["requests"],
                [
                    {"deleteContentRange": {"range": {"startIndex": 1, "endIndex": 3}}},
                    {"insertText": {"location": {"index": 1}, "text": "there"}},
                ],
            )
            return FakeResponse({"writeControl": {"revisionId": "rev-2"}})

        client = GoogleDriveDocsHttpClient(transport=transport)
        result = client.apply_text_mutation(
            {
                "accessToken": "token-1",
                "documentId": "doc-1",
                "mutationType": "REPLACE_TEXT",
                "targetRange": {"startIndex": 1, "endIndex": 3},
                "text": "there",
                "expectedRevision": "rev-1",
                "idempotencyKey": "idem-1",
            }
        )

        self.assertEqual(result["providerOperationId"], "google-docs:doc-1:idem-1")
        self.assertEqual(result["resourceRevision"], "rev-2")
        self.assertIn(":batchUpdate", calls[0].full_url)

    def test_http_error_preserves_status_and_google_reason_for_adapter_mapping(self):
        def transport(_request, _timeout):
            raise HTTPError(
                url="https://docs.googleapis.com/v1/documents/doc-1",
                code=403,
                msg="Forbidden",
                hdrs=None,
                fp=BytesIO(json.dumps({"error": {"status": "PERMISSION_DENIED"}}).encode("utf-8")),
            )

        client = GoogleDriveDocsHttpClient(transport=transport)
        with self.assertRaises(GoogleHttpClientError) as raised:
            client.get_document({"accessToken": "token-1", "documentId": "doc-1"})

        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(raised.exception.reason, "PERMISSION_DENIED")
