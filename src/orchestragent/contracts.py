"""The structure each stage must produce.

Adapters validate their reply against these; the controller never accepts a
bundle it has not checked.
"""

from orchestragent.schema import object_schema

TEXT = {"type": "string"}

PLAN = object_schema({"summary": TEXT})

IMPLEMENT = object_schema(
    {
        "summary": TEXT,
        "files": {
            "type": "array",
            "items": object_schema({"path": TEXT, "content": TEXT}),
        },
    }
)

REVIEW = object_schema({"approved": {"type": "boolean"}, "summary": TEXT})

NOTE = object_schema({"summary": TEXT})

STAGE_SCHEMAS: dict[str, dict[str, object]] = {
    "plan": PLAN,
    "implement": IMPLEMENT,
    "review": REVIEW,
    "test": NOTE,
    "summarize": NOTE,
}
