"""Validation of local contracts and JSON values."""
from pathlib import Path
import json
import os
import sys

PACKAGE = Path(__file__).resolve().parent
# Files shipped with the engine (contracts, agent rules, prototype rules); read-only.
ASSETS = PACKAGE / 'assets'


def _workspace():
    """Where novels/, campaigns/ and corpus/ live: $NOVEL_HOME, else this checkout when run from
    one (a pyproject.toml next to the package), else the current directory for an installed package."""
    configured = os.environ.get('NOVEL_HOME')
    if configured:
        return Path(configured).resolve()
    if (PACKAGE.parent / 'pyproject.toml').is_file():
        return PACKAGE.parent
    return Path.cwd().resolve()


ROOT = _workspace()
if str(PACKAGE.parent) not in sys.path:
    sys.path.insert(0, str(PACKAGE.parent))
from .validation import validate, check_schema

SCHEMAS = ASSETS / 'docs' / 'engine' / 'contracts' / 'v0_2'


def load_schema(kind, stack=()):
    """Resolve local contracts only, with no network access and no cycle.

    The event is defined once for proposal and result.
    Each call returns an independent object, which can be transformed for transport.
"""
    path=(SCHEMAS / (kind+'.schema.json')).resolve()
    if path.parent != SCHEMAS.resolve() or kind in stack:
        raise ValueError('Forbidden or circular contract reference')
    def expand(node):
        if isinstance(node,list):return [expand(n) for n in node]
        if not isinstance(node,dict):return node
        if '$ref' in node:
            ref=node['$ref']
            if set(node)!={'$ref'} or not isinstance(ref,str) or not ref.endswith('.schema.json'):
                raise ValueError('Isolated local contract reference expected')
            return load_schema(ref.removesuffix('.schema.json'),(*stack,kind))
        return {k:expand(v) for k,v in node.items()}
    return expand(json.loads(path.read_text(encoding='utf-8')))


def check_json(value):
    text = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if chr(0x2014) in text:
        raise ValueError('The U+2014 character is forbidden.')
    return text


def check(kind, value):
    check_json(value)
    schema = load_schema(kind)
    check_schema(schema)
    validate(schema, value)
    if kind == 'state':
        events = value['applied_event_ids']
        if len(events) != len(set(events)):
            raise ValueError('Duplicate events in the state')
    if kind == 'proposal':
        if value['blocking_questions'] and value['events']:
            raise ValueError('A blocking question forbids changes')
        ids = [e['event_id'] for e in value['events']]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate events in the proposal')
    return value
