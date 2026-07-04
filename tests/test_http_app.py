import json
import unittest

from ai_assist_google_docs_adapter.errors import GoogleDocsAdapterError
from ai_assist_google_docs_adapter.http_app import GoogleDocsHttpApplication, handle_http_request


AUTH_HEADERS = {
    "Authorization": "Bearer test-session",
    "X-Ai-Assist-Tenant-Id": "tenant-1",
    "X-Ai-Assist-User-Id": "user-1",
}


def response_json(response):
    return json.loads(response["body"].decode("utf-8"))


class GoogleDocsHttpAppTests(unittest.TestCase):
    def test_resources_calls_injected_adapter_and_returns_metadata_only(self):
        adapter = FakeAdapter()
        app = GoogleDocsHttpApplication(adapter=adapter)

        response = app.handle(method="GET", path="/resources", headers=AUTH_HEADERS, query={"pageSize": ["10"]})
        payload = response_json(response)

        self.assertEqual(response["status"], 200)
        self.assertEqual(response["headers"]["Cache-Control"], "no-store")
        self.assertEqual(payload["resources"][0]["resourceId"], "doc-1")
        self.assertEqual(adapter.requests[0]["tenantId"], "tenant-1")
        self.assertEqual(adapter.requests[0]["pageSize"], 10)
        self.assertIsNone(adapter.requests[0]["googleAccountId"])
        self.assertNotIn("content", json.dumps(payload))

    def test_resources_passes_optional_google_account_id_to_token_handoff(self):
        adapter = FakeAdapter()
        app = GoogleDocsHttpApplication(adapter=adapter)

        response = app.handle(
            method="GET",
            path="/resources",
            headers=AUTH_HEADERS,
            query={"googleAccountId": ["account-1"]},
        )

        self.assertEqual(response["status"], 200)
        self.assertEqual(adapter.requests[0]["googleAccountId"], "account-1")

    def test_missing_auth_returns_401(self):
        response = handle_http_request(method="GET", path="/resources", headers={})

        self.assertEqual(response["status"], 401)
        self.assertEqual(response_json(response)["error"]["category"], "AUTHENTICATION")

    def test_missing_runtime_dependency_returns_503_instead_of_not_implemented(self):
        response = handle_http_request(method="GET", path="/resources", headers=AUTH_HEADERS)
        payload = response_json(response)

        self.assertEqual(response["status"], 503)
        self.assertEqual(payload["error"]["code"], "GOOGLE_DOCS_RUNTIME_DEPENDENCY_UNAVAILABLE")
        self.assertEqual(payload["error"]["category"], "DEPENDENCY")

    def test_invalid_page_size_returns_400(self):
        app = GoogleDocsHttpApplication(adapter=FakeAdapter())

        response = app.handle(
            method="GET",
            path="/resources",
            headers=AUTH_HEADERS,
            query={"pageSize": ["abc"]},
        )

        self.assertEqual(response["status"], 400)
        self.assertEqual(response_json(response)["error"]["code"], "VALIDATION_ERROR")

    def test_adapter_auth_error_maps_to_401(self):
        app = GoogleDocsHttpApplication(
            adapter=FailingAdapter(
                GoogleDocsAdapterError(
                    code="TOKEN_RECONNECT_REQUIRED",
                    message="Google OAuth reconnect is required",
                    http_status=401,
                )
            )
        )

        response = app.handle(method="GET", path="/resources", headers=AUTH_HEADERS, query={})

        self.assertEqual(response["status"], 401)
        self.assertEqual(response_json(response)["error"]["category"], "AUTHENTICATION")

    def test_unknown_route_returns_404(self):
        response = handle_http_request(method="GET", path="/unknown", headers=AUTH_HEADERS)

        self.assertEqual(response["status"], 404)
        self.assertEqual(response_json(response)["error"]["code"], "ROUTE_NOT_FOUND")


class FakeAdapter:
    def __init__(self):
        self.requests = []

    def list_resources(self, input_):
        self.requests.append(input_)
        return {
            "resources": [
                {
                    "connector": "google_docs",
                    "provider": "google_docs",
                    "resourceType": "document",
                    "resourceId": "doc-1",
                    "displayName": "Dogfood doc",
                }
            ],
            "nextPageToken": None,
        }


class FailingAdapter:
    def __init__(self, error):
        self.error = error

    def list_resources(self, input_):
        raise self.error


if __name__ == "__main__":
    unittest.main()
