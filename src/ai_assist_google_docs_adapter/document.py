from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .constants import (
    DEFAULT_CONTEXT_TTL_SECONDS,
    PROVIDER,
    SOURCE_TYPE_CONNECTOR_RESOURCE_EXCERPT,
    SOURCE_TYPE_CONNECTOR_SELECTION,
    TRUST_LEVEL_CONNECTOR_VERIFIED,
)
from .errors import (
    RESOURCE_STALE,
    TARGET_CONFLICT,
    VALIDATION_ERROR,
    adapter_error,
)
from .hash import hash_content
from .validation import assert_integer, assert_non_empty_string, assert_plain_object


def normalize_resource(resource: dict[str, Any]) -> dict[str, Any]:
    assert_plain_object(resource, "resource")
    resource_id = resource.get("resourceId") or resource.get("id")
    assert_non_empty_string(resource_id, "resource.resourceId")
    assert_non_empty_string(resource.get("name"), "resource.name")
    revision = resource.get("revisionId") or resource.get("revision") or resource.get("version")
    return {
        "connector": PROVIDER,
        "provider": PROVIDER,
        "resourceType": "document",
        "resourceId": resource_id,
        "displayName": resource["name"],
        "mimeType": resource.get("mimeType") or "application/vnd.google-apps.document",
        "modifiedTime": resource.get("modifiedTime"),
        "externalUrl": resource.get("webViewLink") or resource.get("webUrl"),
        "resourceRevision": revision,
        "revisionMetadata": _revision_metadata(revision=revision, modified_time=resource.get("modifiedTime")),
    }


def document_revision(document: dict[str, Any]) -> str:
    assert_plain_object(document, "document")
    revision = (
        document.get("revisionId")
        or document.get("revision")
        or document.get("version")
        or document.get("modifiedTime")
    )
    assert_non_empty_string(revision, "document.revisionId")
    return revision


def document_text(document: dict[str, Any]) -> str:
    assert_plain_object(document, "document")
    text = document.get("text")
    if isinstance(text, str):
        return text

    structural_content = ((document.get("body") or {}).get("content"))
    if not isinstance(structural_content, list):
        return ""

    parts: list[str] = []
    for block in structural_content:
        elements = ((block.get("paragraph") or {}).get("elements")) if isinstance(block, dict) else None
        if not isinstance(elements, list):
            continue
        for element in elements:
            if isinstance(element, dict):
                parts.append(((element.get("textRun") or {}).get("content")) or "")
    return "".join(parts)


def normalize_read_context(
    input_: dict[str, Any],
    *,
    now: datetime | None = None,
    ttl_seconds: int = DEFAULT_CONTEXT_TTL_SECONDS,
) -> dict[str, Any]:
    assert_plain_object(input_, "input")
    assert_non_empty_string(input_.get("tenantId"), "input.tenantId")
    assert_non_empty_string(input_.get("userId"), "input.userId")
    assert_non_empty_string(input_.get("sessionId"), "input.sessionId")
    assert_non_empty_string(input_.get("resourceId"), "input.resourceId")
    assert_non_empty_string(input_.get("contextMode"), "input.contextMode")
    assert_non_empty_string(input_.get("content"), "input.content")

    captured_at_dt = now or datetime.now(timezone.utc)
    captured_at = _format_datetime(captured_at_dt)
    expires_at = _format_datetime(captured_at_dt + timedelta(seconds=ttl_seconds))
    source_type = (
        SOURCE_TYPE_CONNECTOR_SELECTION
        if input_["contextMode"] == "SELECTION"
        else SOURCE_TYPE_CONNECTOR_RESOURCE_EXCERPT
    )
    anchors = input_.get("anchors") or {}
    revision = input_.get("resourceRevision")
    content = input_["content"]
    revision_metadata = input_.get("revisionMetadata") or _revision_metadata(
        revision=revision,
        modified_time=(input_.get("metadata") or {}).get("modifiedTime"),
    )

    return {
        "contextId": input_.get("contextId"),
        "tenantId": input_["tenantId"],
        "userId": input_["userId"],
        "sessionId": input_["sessionId"],
        "provider": PROVIDER,
        "connector": PROVIDER,
        "resourceRef": {
            "connector": PROVIDER,
            "provider": PROVIDER,
            "resourceId": input_["resourceId"],
            "resourceType": "document",
            **(
                {"displayName": input_["resourceName"]}
                if input_.get("resourceName") is not None
                else {}
            ),
        },
        "contextMode": input_["contextMode"],
        "sourceType": source_type,
        "trustLevel": TRUST_LEVEL_CONNECTOR_VERIFIED,
        "content": content,
        "contentHash": hash_content(content),
        "anchors": anchors,
        "resourceRevision": revision,
        "revisionMetadata": revision_metadata,
        "metadata": input_.get("metadata") or {},
        "provenance": {
            "sourceType": source_type,
            "trustLevel": TRUST_LEVEL_CONNECTOR_VERIFIED,
            "connector": PROVIDER,
            "resourceId": input_["resourceId"],
            "resourceVersion": revision,
            **(
                {"selectionAnchor": anchors["selectionAnchor"]}
                if anchors.get("selectionAnchor") is not None
                else {}
            ),
            "capturedAt": captured_at,
            "clientSupplied": False,
            "connectorVerified": True,
        },
        "capturedAt": captured_at,
        "expiresAt": expires_at,
        "clientSupplied": False,
        "connectorVerified": True,
    }


def normalize_range(range_: dict[str, Any], field_name: str = "targetRange") -> dict[str, int]:
    assert_plain_object(range_, field_name)
    assert_integer(range_.get("startIndex"), f"{field_name}.startIndex")
    assert_integer(range_.get("endIndex"), f"{field_name}.endIndex")
    if range_["startIndex"] < 0 or range_["endIndex"] < range_["startIndex"]:
        raise adapter_error(
            VALIDATION_ERROR,
            f"{field_name} is invalid",
            details={"field": field_name, "range": range_},
        )
    return {"startIndex": range_["startIndex"], "endIndex": range_["endIndex"]}


def normalize_anchor(anchor: dict[str, Any], field_name: str = "targetAnchor") -> dict[str, int]:
    assert_plain_object(anchor, field_name)
    assert_integer(anchor.get("index"), f"{field_name}.index")
    if anchor["index"] < 0:
        raise adapter_error(
            VALIDATION_ERROR,
            f"{field_name}.index must be non-negative",
            details={"field": f"{field_name}.index"},
        )
    return {"index": anchor["index"]}


def verify_replace_target(
    *,
    text: str,
    current_revision: str,
    expected_revision: str,
    target_range: dict[str, Any],
    original_text_hash: str,
) -> dict[str, Any]:
    _assert_revision(current_revision, expected_revision)
    range_ = normalize_range(target_range)
    assert_non_empty_string(original_text_hash, "originalTextHash")
    current_text = provider_indexed_slice(
        text,
        range_["startIndex"],
        range_["endIndex"],
        unresolved_reason="TARGET_RANGE_UNRESOLVED",
    )
    current_hash = hash_content(current_text)
    if current_hash != original_text_hash:
        raise adapter_error(
            TARGET_CONFLICT,
            "target text hash does not match original text",
            http_status=409,
            details={"reason": "ORIGINAL_TEXT_HASH_MISMATCH", "currentHash": current_hash},
        )
    return {"targetRange": range_, "currentText": current_text, "currentHash": current_hash}


def verify_insert_target(
    *,
    text: str,
    current_revision: str,
    expected_revision: str,
    target_anchor: dict[str, Any],
) -> dict[str, Any]:
    _assert_revision(current_revision, expected_revision)
    anchor = normalize_anchor(target_anchor)
    provider_index_to_py_offset(text, anchor["index"], unresolved_reason="TARGET_ANCHOR_UNRESOLVED")
    return {"targetAnchor": anchor}


def provider_indexed_slice(
    text: str,
    start_index: int,
    end_index: int,
    *,
    unresolved_reason: str,
) -> str:
    start_offset = provider_index_to_py_offset(text, start_index, unresolved_reason=unresolved_reason)
    end_offset = provider_index_to_py_offset(text, end_index, unresolved_reason=unresolved_reason)
    return text[start_offset:end_offset]


def provider_index_to_py_offset(text: str, index: int, *, unresolved_reason: str) -> int:
    units_seen = 0
    for offset, char in enumerate(text):
        if units_seen == index:
            return offset
        char_units = _utf16_code_unit_length(char)
        next_units_seen = units_seen + char_units
        if units_seen < index < next_units_seen:
            _raise_unresolved_provider_index(unresolved_reason)
        units_seen = next_units_seen

    if units_seen == index:
        return len(text)
    _raise_unresolved_provider_index(unresolved_reason)


def _utf16_code_unit_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _raise_unresolved_provider_index(reason: str) -> None:
    raise adapter_error(
        TARGET_CONFLICT,
        "provider index no longer resolves",
        http_status=409,
        details={"reason": reason},
    )


def _assert_revision(current_revision: str, expected_revision: str) -> None:
    assert_non_empty_string(current_revision, "currentRevision")
    assert_non_empty_string(expected_revision, "expectedRevision")
    if current_revision != expected_revision:
        raise adapter_error(
            RESOURCE_STALE,
            "document revision is stale",
            http_status=409,
            details={"expectedRevision": expected_revision, "currentRevision": current_revision},
        )


def _format_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _revision_metadata(*, revision: Any, modified_time: Any = None) -> dict[str, Any]:
    metadata = {"provider": PROVIDER}
    if revision is not None:
        metadata["revisionId"] = revision
    if modified_time is not None:
        metadata["modifiedTime"] = modified_time
    return metadata
