"""Structured extraction: turn a document into JSON matching a caller-provided schema."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple

EXTRACTION_TEMPLATE = """Extract information from the document below into a JSON object that follows this JSON Schema:

{schema}
{instructions}
Rules: use only information stated in the document; use null when a value is absent; reply with the JSON object only.

Document:
{text}"""

RETRY_TEMPLATE = """{previous_prompt}

Your previous reply was invalid: {errors}. Reply again with a corrected JSON object only."""

MAX_TEXT_CHARS = 30000
TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "null": lambda v: v is None,
}


def validate(data: Any, schema: Dict[str, Any], path: str = "$") -> List[str]:
    """Validate the common JSON Schema keywords (type, required, properties, items, enum)."""
    errors: List[str] = []
    expected = schema.get("type")
    if expected:
        types = expected if isinstance(expected, list) else [expected]
        if not any(TYPE_CHECKS.get(t, lambda v: True)(data) for t in types):
            return [f"{path} should be {' or '.join(types)}"]
    if "enum" in schema and data not in schema["enum"]:
        errors.append(f"{path} should be one of {schema['enum']}")
    if isinstance(data, dict):
        for key in schema.get("required", []):
            if key not in data:
                errors.append(f"{path}.{key} is required")
        for key, sub_schema in schema.get("properties", {}).items():
            if key in data and data[key] is not None:
                errors.extend(validate(data[key], sub_schema, f"{path}.{key}"))
    if isinstance(data, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(data):
            errors.extend(validate(item, schema["items"], f"{path}[{index}]"))
    return errors


def _parse_json(reply: str) -> Any:
    match = re.search(r"\{.*\}", reply or "", re.DOTALL)
    if not match:
        raise ValueError("no JSON object found")
    return json.loads(match.group(0))


def extract(llm_service, text: str, schema: Dict[str, Any], instructions: str = "", *, attempts: int = 2) -> Dict[str, Any]:
    """Ask the LLM for schema-conforming JSON, retrying once with the validation errors."""
    prompt = EXTRACTION_TEMPLATE.format(
        schema=json.dumps(schema, ensure_ascii=False, indent=2),
        instructions=f"\nInstructions: {instructions}\n" if instructions else "",
        text=text[:MAX_TEXT_CHARS],
    )
    errors: List[str] = []
    data: Any = None
    current = prompt
    for attempt in range(1, attempts + 1):
        reply = llm_service.generate(current, temperature=0.0, max_tokens=4000)
        try:
            data = _parse_json(reply)
            errors = validate(data, schema)
        except (ValueError, json.JSONDecodeError) as exc:
            data, errors = None, [f"invalid JSON ({exc})"]
        if not errors:
            return {"data": data, "valid": True, "errors": [], "attempts": attempt}
        current = RETRY_TEMPLATE.format(previous_prompt=prompt, errors="; ".join(errors))
    return {"data": data, "valid": False, "errors": errors, "attempts": attempts}


def check_schema(schema: Any) -> Tuple[bool, str]:
    if not isinstance(schema, dict) or schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
        return False, "schema must be a JSON Schema object with type 'object' and 'properties'"
    return True, ""
