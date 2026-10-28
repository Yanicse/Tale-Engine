"""Local JSON validation, independent of the verification scripts."""
import re
import math

KEYWORDS = {
    "$schema", "title", "description", "type", "properties", "required",
    "additionalProperties", "items", "minItems", "minLength", "minimum",
    "pattern", "enum", "const", "maxLength", "maxItems", "maximum",
}


def check_schema(schema: dict) -> None:
    unknown = set(schema) - KEYWORDS
    if unknown:
        raise ValueError(f"Unsupported keywords: {sorted(unknown)}")
    for child in schema.get("properties", {}).values():
        check_schema(child)
    for key in ("items", "additionalProperties"):
        if isinstance(schema.get(key), dict):
            check_schema(schema[key])


def validate(schema: dict, value, path: str = "$") -> None:
    """Explicit subset of JSON Schema, limited to the local contracts."""
    checks = {
        "object": lambda x: isinstance(x, dict),
        "array": lambda x: isinstance(x, list),
        "string": lambda x: isinstance(x, str),
        "integer": lambda x: type(x) is int,
        "number": lambda x: type(x) in (int,float) and math.isfinite(x),
        "boolean": lambda x: type(x) is bool,
        "null": lambda x: x is None,
    }
    expected = schema.get("type")
    types = expected if isinstance(expected, list) else [expected]
    if expected is not None and not any(t in checks and checks[t](value) for t in types):
        raise ValueError(f"{path}: type expected {expected}")
    if "const" in schema and (type(value) is not type(schema["const"]) or value != schema["const"]):
        raise ValueError(f"{path}: constant incorrect")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: value outside the enumeration")
    if type(value) in (int,float) and value < schema.get("minimum", value):
        raise ValueError(f"{path}: minimum not met")
    if type(value) in (int,float) and value > schema.get('maximum',value):
        raise ValueError(f"{path}: maximum not met")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise ValueError(f"{path}: text trop short")
        if len(value) > schema.get('maxLength',len(value)):
            raise ValueError(f"{path}: text trop long")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            raise ValueError(f"{path}: pattern not matched")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise ValueError(f"{path}: liste trop short")
        if len(value) > schema.get('maxItems',len(value)):
            raise ValueError(f"{path}: liste trop long")
        for i, child in enumerate(value):
            validate(schema.get("items", {}), child, f"{path}[{i}]")
    if isinstance(value, dict):
        missing = set(schema.get("required", [])) - set(value)
        if missing:
            raise ValueError(f"{path}: fields absents {sorted(missing)}")
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, child in value.items():
            if key in props:
                validate(props[key], child, f"{path}.{key}")
            elif extra is False:
                raise ValueError(f"{path}: field unexpected {key}")
            elif isinstance(extra, dict):
                validate(extra, child, f"{path}.{key}")

