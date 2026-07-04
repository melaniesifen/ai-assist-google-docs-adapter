from __future__ import annotations

from typing import Any

from .adapter import GoogleDocsAdapter
from .constants import MUTATION_TYPE_INSERT_TEXT, MUTATION_TYPE_REPLACE_TEXT
from .errors import RESOURCE_STALE, TARGET_CONFLICT, GoogleDocsAdapterError


ACTION_TYPE_TO_MUTATION_TYPE = {
    "replace_text": MUTATION_TYPE_REPLACE_TEXT,
    "insert_text": MUTATION_TYPE_INSERT_TEXT,
    MUTATION_TYPE_REPLACE_TEXT: MUTATION_TYPE_REPLACE_TEXT,
    MUTATION_TYPE_INSERT_TEXT: MUTATION_TYPE_INSERT_TEXT,
}


class GoogleDocsOrchestrationConnector:
    def __init__(self, adapter: GoogleDocsAdapter) -> None:
        if not isinstance(adapter, GoogleDocsAdapter):
            raise TypeError("adapter must be a GoogleDocsAdapter")
        self.adapter = adapter

    def validate_target(self, action: dict[str, Any]) -> dict[str, Any]:
        try:
            verification = self.adapter.verify_target(_verification_request(action))
        except GoogleDocsAdapterError as error:
            if error.code in {RESOURCE_STALE, TARGET_CONFLICT}:
                return {
                    "valid": False,
                    "reasonCode": _reason_code(error),
                    "conflictDetails": _safe_conflict_details(error),
                }
            raise
        return {
            "valid": True,
            "verifiedTarget": {
                "resourceId": action["resourceId"],
                "resourceRevision": verification["resourceRevision"],
                **({"targetRange": verification["targetRange"]} if verification.get("targetRange") is not None else {}),
                **({"targetAnchor": verification["targetAnchor"]} if verification.get("targetAnchor") is not None else {}),
                **({"originalTextHash": action["originalTextHash"]} if action.get("originalTextHash") is not None else {}),
            },
        }

    def apply_action(self, request: dict[str, Any]) -> dict[str, Any]:
        _required_object(request, "request")
        action = _required_object(request.get("action"), "action")
        payload = _required_object(request.get("payload"), "payload")
        verified_target = _required_object(request.get("verifiedTarget"), "verifiedTarget")
        _assert_verified_target_matches_action(action, verified_target)
        text = _payload_text(payload, action)
        result = self.adapter.apply_replace_insert(
            {
                "tenantId": action["tenantId"],
                "userId": action["userId"],
                "sessionId": action.get("sessionId"),
                "resourceId": action["resourceId"],
                "contextMode": action.get("contextMode"),
                "consentGrantId": action.get("consentGrantId"),
                "mutationType": _mutation_type(action),
                "expectedRevision": action["resourceRevision"],
                "targetRange": action.get("targetRange"),
                "targetAnchor": action.get("targetAnchor"),
                "originalTextHash": action.get("originalTextHash"),
                "text": text,
                "idempotencyKey": request["idempotencyKey"],
            }
        )
        if result.get("status") == "CONFLICTED":
            return {
                "status": "CONFLICTED",
                "reasonCode": _conflict_reason_from_result(result),
                "conflictDetails": result.get("conflictDetails", {}),
            }
        return {
            "providerOperationId": result.get("providerOperationId"),
            "resourceRevision": result.get("resourceRevision"),
        }


def _verification_request(action: dict[str, Any]) -> dict[str, Any]:
    _required_object(action, "action")
    return {
        "tenantId": action["tenantId"],
        "userId": action["userId"],
        "sessionId": action.get("sessionId"),
        "resourceId": action["resourceId"],
        "contextMode": action.get("contextMode"),
        "consentGrantId": action.get("consentGrantId"),
        "mutationType": _mutation_type(action),
        "expectedRevision": action["resourceRevision"],
        "targetRange": action.get("targetRange"),
        "targetAnchor": action.get("targetAnchor"),
        "originalTextHash": action.get("originalTextHash"),
    }


def _mutation_type(action: dict[str, Any]) -> str:
    mutation_type = ACTION_TYPE_TO_MUTATION_TYPE.get(action.get("actionType"))
    if mutation_type is None:
        mutation_type = ACTION_TYPE_TO_MUTATION_TYPE.get(action.get("mutationType"))
    if mutation_type is None:
        raise ValueError("actionType is not supported")
    return mutation_type


def _payload_text(payload: dict[str, Any], action: dict[str, Any]) -> str:
    fields = (
        ("proposedText", "replacementText", "text")
        if _mutation_type(action) == MUTATION_TYPE_REPLACE_TEXT
        else ("insertText", "proposedText", "text")
    )
    for field_name in fields:
        value = payload.get(field_name)
        if isinstance(value, str) and len(value.strip()) > 0:
            return value
    raise ValueError("payload text is required")


def _assert_verified_target_matches_action(action: dict[str, Any], verified_target: dict[str, Any]) -> None:
    for field_name in ("resourceId", "resourceRevision", "targetRange", "targetAnchor", "originalTextHash"):
        if verified_target.get(field_name) != action.get(field_name):
            raise ValueError(f"verifiedTarget.{field_name} does not match action")


def _reason_code(error: GoogleDocsAdapterError) -> str:
    reason = error.details.get("reason")
    if isinstance(reason, str) and reason:
        return reason
    return error.code


def _safe_conflict_details(error: GoogleDocsAdapterError) -> dict[str, Any]:
    details = {"connectorCode": error.code, "reasonCode": _reason_code(error)}
    for source, target in (
        ("expectedRevision", "expectedRevision"),
        ("currentRevision", "currentRevision"),
        ("resourceId", "resourceId"),
        ("expectedResourceId", "resourceId"),
    ):
        value = error.details.get(source)
        if value is not None:
            details[target] = value
    return details


def _conflict_reason_from_result(result: dict[str, Any]) -> str:
    details = result.get("conflictDetails") if isinstance(result.get("conflictDetails"), dict) else {}
    reason = details.get("reason")
    if isinstance(reason, str) and reason:
        return reason
    code = details.get("code")
    return code if isinstance(code, str) and code else TARGET_CONFLICT


def _required_object(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object")
    return value
