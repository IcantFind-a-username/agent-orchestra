"""A small JSON Schema subset, enough to check adapter output without dependencies.

Supports the keywords the stage contracts use: type, properties, required,
additionalProperties, items and enum. Anything outside the subset is rejected
rather than silently ignored, so a contract cannot be quietly weakened.
"""

from collections.abc import Mapping
from typing import Any

_SUPPORTED = {"type", "properties", "required", "additionalProperties", "items", "enum"}
_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "boolean": (bool,),
    "number": (int, float),
    "integer": (int,),
}


def _type_name(value: object) -> str:
    for name, types in _TYPES.items():
        if name == "integer" and isinstance(value, bool):
            continue
        if isinstance(value, types):
            return name
    return type(value).__name__


def validate(value: object, schema: object, path: str = "$") -> None:
    """Raise ValueError with a path when `value` does not match `schema`."""
    if not isinstance(schema, Mapping):
        raise ValueError(f"{path}: schema must be an object")
    unknown = set(schema) - _SUPPORTED
    if unknown:
        raise ValueError(f"{path}: unsupported schema keywords {sorted(unknown)}")

    expected = schema.get("type")
    if expected is not None:
        if not isinstance(expected, str) or expected not in _TYPES:
            raise ValueError(f"{path}: unsupported type {expected!r}")
        if expected == "integer" and isinstance(value, bool):
            raise ValueError(f"{path}: expected integer, got boolean")
        if not isinstance(value, _TYPES[expected]):
            raise ValueError(f"{path}: expected {expected}, got {_type_name(value)}")

    options = schema.get("enum")
    if options is not None:
        if not isinstance(options, list):
            raise ValueError(f"{path}: enum must be an array")
        if value not in options:
            raise ValueError(f"{path}: expected one of {options!r}")

    if isinstance(value, Mapping):
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            raise ValueError(f"{path}: properties must be an object")
        required = schema.get("required", [])
        if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            raise ValueError(f"{path}: required must be an array of strings")
        for key in required:
            if key not in value:
                raise ValueError(f"{path}: missing required property {key!r}")
        extra = set(value) - set(properties)
        if extra and schema.get("additionalProperties") is False:
            raise ValueError(f"{path}: unexpected properties {sorted(extra)}")
        for key, subschema in properties.items():
            if key in value:
                validate(value[key], subschema, f"{path}.{key}")

    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            validate(item, schema["items"], f"{path}[{index}]")


def object_schema(properties: dict[str, object]) -> dict[str, object]:
    """Every property required, nothing else allowed."""
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def as_object(value: Any, path: str = "$") -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected object")
    return value
