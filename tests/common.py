import unittest
from datetime import datetime, timezone

from ai_assist_google_docs_adapter import (
    ERROR_CODES,
    GOOGLE_OAUTH_SCOPE_DOCUMENTS,
    GOOGLE_OAUTH_SCOPE_DOCUMENTS_READONLY,
    GOOGLE_OAUTH_SCOPE_DRIVE_METADATA_READONLY,
    GoogleDocsAdapter,
    GoogleDocsAdapterError,
)


NOW = datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)
IDENTITY = {"tenantId": "tenant-1", "userId": "user-1"}
ALL_SCOPES = [
    GOOGLE_OAUTH_SCOPE_DRIVE_METADATA_READONLY,
    GOOGLE_OAUTH_SCOPE_DOCUMENTS_READONLY,
    GOOGLE_OAUTH_SCOPE_DOCUMENTS,
]


class StatusError(Exception):
    def __init__(self, status=None, code=None, name=None, reason=None, content=None, error_details=None):
        super().__init__("provider error")
        self.status = status
        self.code = code
        self.reason = reason
        self.content = content
        self.error_details = error_details
        if name is not None:
            self.name = name


class ResponseStatusError(Exception):
    def __init__(self, status, *, content=None):
        super().__init__("provider response error")
        self.resp = type("Response", (), {"status": status})()
        self.content = content


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


class AdapterTestCase(unittest.TestCase):
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


__all__ = [
    "ALL_SCOPES",
    "ERROR_CODES",
    "IDENTITY",
    "NOW",
    "AdapterTestCase",
    "FakeDependencies",
    "ResponseStatusError",
    "StatusError",
    "adapter_with",
    "token_response",
    "token_response_without_access_token",
]
