import pytest

from orchestragent.schema import object_schema, validate

BUNDLE = object_schema(
    {
        "summary": {"type": "string"},
        "files": {
            "type": "array",
            "items": object_schema({"path": {"type": "string"}, "content": {"type": "string"}}),
        },
    }
)

VALID = {"summary": "ok", "files": [{"path": "a.py", "content": "x = 1\n"}]}


def test_a_valid_payload_passes() -> None:
    validate(VALID, BUNDLE)


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({}, "missing required property 'summary'"),
        ({"summary": "ok"}, "missing required property 'files'"),
        ({"summary": 1, "files": []}, "expected string"),
        ({"summary": "ok", "files": {}}, "expected array"),
        ({"summary": "ok", "files": [{}]}, "missing required property 'path'"),
        ({"summary": "ok", "files": [], "extra": 1}, "unexpected properties"),
    ],
)
def test_invalid_payloads_report_a_path(payload: object, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        validate(payload, BUNDLE)


def test_error_messages_point_at_the_offending_value() -> None:
    with pytest.raises(ValueError, match=r"\$\.files\[0\]\.path"):
        validate({"summary": "ok", "files": [{"path": 1, "content": ""}]}, BUNDLE)


@pytest.mark.parametrize("value", [True, False])
def test_booleans_are_not_integers(value: bool) -> None:
    with pytest.raises(ValueError, match="expected integer"):
        validate(value, {"type": "integer"})


def test_enum_is_enforced() -> None:
    with pytest.raises(ValueError, match="expected one of"):
        validate("c", {"type": "string", "enum": ["a", "b"]})


def test_unsupported_keywords_are_rejected_rather_than_ignored() -> None:
    with pytest.raises(ValueError, match="unsupported schema keywords"):
        validate("x", {"type": "string", "pattern": "^x$"})


def test_malformed_schema_is_rejected() -> None:
    with pytest.raises(ValueError, match="schema must be an object"):
        validate("x", ["not", "a", "schema"])
    with pytest.raises(ValueError, match="unsupported type"):
        validate("x", {"type": "date"})
