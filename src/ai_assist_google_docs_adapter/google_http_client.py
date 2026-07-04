from __future__ import annotations

import json
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DRIVE_API_BASE_URL = "https://www.googleapis.com/drive/v3"
DOCS_API_BASE_URL = "https://docs.googleapis.com/v1"
GOOGLE_DOC_MIME_TYPE = "application/vnd.google-apps.document"


class GoogleHttpClientError(Exception):
    def __init__(self, *, status: int | None = None, reason: str | None = None, content: str | None = None):
        super().__init__("Google HTTP request failed")
        self.status = status
        self.reason = reason
        self.content = content


class GoogleDriveDocsHttpClient:
    def __init__(
        self,
        *,
        drive_api_base_url: str = DRIVE_API_BASE_URL,
        docs_api_base_url: str = DOCS_API_BASE_URL,
        transport: Callable[[Request, float | None], Any] | None = None,
        timeout_seconds: float | None = 10.0,
    ) -> None:
        self.drive_api_base_url = drive_api_base_url.rstrip("/")
        self.docs_api_base_url = docs_api_base_url.rstrip("/")
        self.transport = transport or _default_transport
        self.timeout_seconds = timeout_seconds

    def list_documents(self, input_: dict[str, Any]) -> dict[str, Any]:
        access_token = _required_string(input_, "accessToken")
        page_size = input_.get("pageSize") or 25
        query = {
            "pageSize": page_size,
            "q": f"mimeType='{GOOGLE_DOC_MIME_TYPE}' and trashed=false",
            "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,webViewLink)",
        }
        if input_.get("pageToken"):
            query["pageToken"] = input_["pageToken"]
        payload = self._request_json(
            "GET",
            f"{self.drive_api_base_url}/files?{urlencode(query)}",
            access_token=access_token,
        )
        return {
            "resources": payload.get("files") or [],
            "nextPageToken": payload.get("nextPageToken"),
        }

    def get_document(self, input_: dict[str, Any]) -> dict[str, Any]:
        access_token = _required_string(input_, "accessToken")
        document_id = _required_string(input_, "documentId")
        fields = (
            "documentId,title,revisionId,"
            "body(content(startIndex,endIndex,paragraph(elements(startIndex,endIndex,textRun(content)))))"
        )
        payload = self._request_json(
            "GET",
            f"{self.docs_api_base_url}/documents/{document_id}?{urlencode({'fields': fields})}",
            access_token=access_token,
        )
        return {
            "documentId": payload.get("documentId") or document_id,
            "title": payload.get("title"),
            "revisionId": payload.get("revisionId"),
            "text": _extract_document_text(payload),
            "body": payload.get("body"),
        }

    def apply_text_mutation(self, input_: dict[str, Any]) -> dict[str, Any]:
        access_token = _required_string(input_, "accessToken")
        document_id = _required_string(input_, "documentId")
        mutation_type = _required_string(input_, "mutationType")
        text = _required_string(input_, "text")
        expected_revision = _required_string(input_, "expectedRevision")
        requests = _batch_update_requests(mutation_type, text, input_)
        payload = self._request_json(
            "POST",
            f"{self.docs_api_base_url}/documents/{document_id}:batchUpdate",
            access_token=access_token,
            body={
                "requests": requests,
                "writeControl": {"requiredRevisionId": expected_revision},
            },
        )
        write_control = payload.get("writeControl") if isinstance(payload.get("writeControl"), dict) else {}
        return {
            "providerOperationId": _provider_operation_id(document_id, input_.get("idempotencyKey")),
            "resourceRevision": write_control.get("requiredRevisionId") or write_control.get("revisionId"),
        }

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        access_token: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                **({"Content-Type": "application/json"} if body is not None else {}),
            },
        )
        try:
            response = self.transport(request, self.timeout_seconds)
            raw = response.read()
        except HTTPError as error:
            content = error.read().decode("utf-8", errors="ignore")
            raise GoogleHttpClientError(
                status=error.code,
                reason=_google_error_reason(content),
                content=content,
            ) from error
        except URLError as error:
            raise GoogleHttpClientError(reason=getattr(error, "reason", None) or "unavailable") from error
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))


def _batch_update_requests(mutation_type: str, text: str, input_: dict[str, Any]) -> list[dict[str, Any]]:
    if mutation_type == "REPLACE_TEXT":
        target_range = input_.get("targetRange") or {}
        start_index = target_range.get("startIndex")
        end_index = target_range.get("endIndex")
        return [
            {"deleteContentRange": {"range": {"startIndex": start_index, "endIndex": end_index}}},
            {"insertText": {"location": {"index": start_index}, "text": text}},
        ]
    if mutation_type == "INSERT_TEXT":
        target_anchor = input_.get("targetAnchor") or {}
        return [{"insertText": {"location": {"index": target_anchor.get("index")}, "text": text}}]
    raise ValueError("unsupported mutation type")


def _extract_document_text(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    for block in ((payload.get("body") or {}).get("content") or []):
        for element in ((block.get("paragraph") or {}).get("elements") or []):
            text_run = element.get("textRun") if isinstance(element, dict) else None
            content = text_run.get("content") if isinstance(text_run, dict) else None
            if isinstance(content, str):
                parts.append(content)
    return "".join(parts)


def _google_error_reason(content: str) -> str | None:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    error = payload.get("error") if isinstance(payload.get("error"), dict) else payload
    if isinstance(error.get("status"), str):
        return error["status"]
    details = error.get("errors")
    if isinstance(details, list) and details and isinstance(details[0], dict):
        reason = details[0].get("reason")
        if isinstance(reason, str):
            return reason
    return None


def _provider_operation_id(document_id: str, idempotency_key: Any) -> str:
    if isinstance(idempotency_key, str) and idempotency_key:
        return f"google-docs:{document_id}:{idempotency_key}"
    return f"google-docs:{document_id}:batchUpdate"


def _required_string(input_: dict[str, Any], field_name: str) -> str:
    value = input_.get(field_name)
    if not isinstance(value, str) or len(value.strip()) == 0:
        raise ValueError(f"{field_name} is required")
    return value


def _default_transport(request: Request, timeout: float | None) -> Any:
    return urlopen(request, timeout=timeout)
