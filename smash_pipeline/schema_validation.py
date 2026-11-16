from __future__ import annotations

import json
import re
from typing import Any


class JsonSchemaValidationError(ValueError):
    pass


def validate(instance: Any, schema: dict, root: dict | None = None, path: str = "$") -> None:
    """Validate the JSON Schema subset used by this project without dependencies."""
    root = root or schema
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/"):
            raise JsonSchemaValidationError(f"{path}: only local $ref is supported")
        target: Any = root
        for part in ref[2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        validate(instance, target, root, path)
        return

    expected = schema.get("type")
    if expected is not None:
        expected_types = expected if isinstance(expected, list) else [expected]
        checks = {
            "object": lambda v: isinstance(v, dict),
            "array": lambda v: isinstance(v, list),
            "string": lambda v: isinstance(v, str),
            "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
            "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
            "boolean": lambda v: isinstance(v, bool),
            "null": lambda v: v is None,
        }
        if not any(checks[t](instance) for t in expected_types):
            raise JsonSchemaValidationError(f"{path}: expected {expected_types}, got {type(instance).__name__}")

    if "const" in schema and instance != schema["const"]:
        raise JsonSchemaValidationError(f"{path}: value does not match const")
    if "enum" in schema and instance not in schema["enum"]:
        raise JsonSchemaValidationError(f"{path}: value not in enum")
    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            raise JsonSchemaValidationError(f"{path}: string shorter than minLength")
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            raise JsonSchemaValidationError(f"{path}: string does not match pattern")
    if isinstance(instance, (int, float)) and not isinstance(instance, bool) and "minimum" in schema and instance < schema["minimum"]:
        raise JsonSchemaValidationError(f"{path}: number below minimum")
    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            raise JsonSchemaValidationError(f"{path}: array shorter than minItems")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(value, sort_keys=True) for value in instance]
            if len(encoded) != len(set(encoded)):
                raise JsonSchemaValidationError(f"{path}: array items are not unique")
        if "items" in schema:
            for index, value in enumerate(instance):
                validate(value, schema["items"], root, f"{path}[{index}]")
    if isinstance(instance, dict):
        missing = set(schema.get("required", [])) - instance.keys()
        if missing:
            raise JsonSchemaValidationError(f"{path}: missing {sorted(missing)}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extras = instance.keys() - properties.keys()
            if extras:
                raise JsonSchemaValidationError(f"{path}: unexpected properties {sorted(extras)}")
        for key, value in instance.items():
            if key in properties:
                validate(value, properties[key], root, f"{path}.{key}")

