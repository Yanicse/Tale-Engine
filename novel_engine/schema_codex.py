"""Explicit boundary between the local JSON Schema and the Codex structured output."""
from copy import deepcopy
import hashlib
import json

from .validation import check_schema


def compile_schema(schema):
    """Locally refuse an untyped contract before starting the CLI.

    Large reference lists stay checked locally. They must not
    exceed the service budgets as the campaign grows.
    Small structural enumerations stay constrained remotely.
"""
    check_schema(schema)
    output = deepcopy(schema)
    enums = []
    properties = 0

    def visit(node, path='$', depth=0):
        nonlocal properties
        types = node.get('type')
        types = types if isinstance(types, list) else [types]
        if not types or any(t not in ('object', 'array', 'string', 'integer', 'number', 'boolean', 'null') for t in types):
            raise ValueError(path + ': explicit JSON type required for Codex')
        if depth > 10:
            raise ValueError(path + ': excessive nesting for Codex')
        if 'const' in node:
            node['enum'] = [node.pop('const')]
        if 'enum' in node:
            if not node['enum']:
                raise ValueError(path + ': enum empty')
            enums.append(node)
        if 'object' in types:
            props = node.get('properties', {})
            properties += len(props)
            if node.get('additionalProperties') is not False or set(node.get('required', [])) != set(props):
                raise ValueError(path + ': closed Codex object with all required fields expected')
            for key, child in props.items():
                visit(child, path + '.' + key, depth + 1)
        if 'array' in types:
            if not isinstance(node.get('items'), dict):
                raise ValueError(path + ': typed items required')
            visit(node['items'], path + '[]', depth + 1)

    visit(output)
    if properties > 5000:
        raise ValueError('Too many properties for Codex')
    # Dynamic lists are repeated for causes, files and agenda.
    # Keep a margin below the global limits of the service.
    for node in enums:
        values = node.get('enum', [])
        if len(values) > 128 or sum(len(str(v)) for v in values) > 7500:
            node.pop('enum', None)
    if sum(len(n.get('enum', [])) for n in enums) > 1000:
        raise ValueError('Too many enumerated values for Codex')
    encoded = json.dumps(output, ensure_ascii=True, sort_keys=True)
    return output, hashlib.sha256(encoded.encode()).hexdigest()
