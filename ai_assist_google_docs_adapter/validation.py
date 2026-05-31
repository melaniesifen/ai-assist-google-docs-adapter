from __future__ import annotations

from typing import Any

from .errors import VALIDATION_ERROR, adapter_error


def assert_plain_object(value: Any, field_name: str) -> None:
    if not isinstance(value, dict):
        raise adapter_error(
            VALIDATION_ERROR,
            f"{field_name} must be an object",
            details={"field": field_name},
        )


def assert_non_empty_string(value: Any, field_name: str) -> None:
    if not isinstance(value, str) or len(value.strip()) == 0:
        raise adapter_error(
            VALIDATION_ERROR,
            f"{field_name} must be a non-empty string",
            details={"field": field_name},
        )


def assert_integer(value: Any, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise adapter_error(
            VALIDATION_ERROR,
            f"{field_name} must be an integer",
            details={"field": field_name},
        )


def assert_identity(input_: dict[str, Any]) -> None:
    assert_plain_object(input_, "input")
    assert_non_empty_string(input_.get("tenantId"), "input.tenantId")
    assert_non_empty_string(input_.get("userId"), "input.userId")
