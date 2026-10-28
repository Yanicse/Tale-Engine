"""Narrative commands: the model decides, the engine keeps the metadata."""
from copy import deepcopy
import json
from .prompts import load as load_prompt


METADATA_ERROR = 'An existing deadline does not redefine its metadata.'
METADATA_CORRECTION = load_prompt('agenda_protocol.metadata_correction')


def metadata_redefinitions(commands, packet):
    known = {entry['entry_id'] for entry in packet.get('events', [])}
    return [{'index': index, 'entry_id': command['entry_id']}
            for index, command in enumerate(commands)
            if isinstance(command, dict) and command.get('entry_id') in known
            and command.get('creation') is not None]


def retry_metadata_correction(receipt):
    """Private diagnostic of the receipt, without rewriting or applying its decisions."""
    response = receipt.get('received')
    try:
        for _ in range(2):
            if not isinstance(response, str):
                break
            response = json.loads(response)
    except ValueError:
        return None
    if not isinstance(response, dict) or not isinstance(response.get('agenda_updates'), list):
        return None
    conflicts = metadata_redefinitions(response['agenda_updates'], receipt.get('packet', {}).get('world_agenda', {}))
    if not conflicts:
        return None
    return {'code': 'creation_on_existing_deadline', 'commands_to_fix': conflicts,
            'instruction': METADATA_CORRECTION}


def command_schema(full_schema):
    fields = deepcopy(full_schema['items']['properties'])
    creation_keys = ('title', 'actor', 'location', 'kind', 'conditions', 'audience', 'interrupt')
    creation = {key: fields[key] for key in creation_keys}
    properties = {key: fields[key] for key in
                  ('entry_id', 'status', 'reason', 'references', 'outcome_event_index')}
    properties['due_minute'] = {'type': ['integer', 'null'], 'minimum': 0}
    properties['creation'] = {'type': ['object', 'null'], 'properties': creation,
                              'required': list(creation), 'additionalProperties': False}
    return {'type': 'array', 'maxItems': full_schema['maxItems'], 'items': {
        'type': 'object', 'properties': properties,
        'required': list(properties), 'additionalProperties': False}}


def materialize(commands, packet):
    """Develop exclusively from the context read, never from a fresh state.

    The kept revision guarantees that a concurrent write will be refused.
    A resolution has no planned date to copy: it cites an event.
"""
    if metadata_redefinitions(commands, packet):
        raise ValueError(METADATA_ERROR+' '+METADATA_CORRECTION)
    known = {entry['entry_id']: entry for entry in packet['events']}
    output = []
    for command in commands:
        old = known.get(command['entry_id'])
        creation = command['creation']
        if old is not None:
            update = {key: deepcopy(old[key]) for key in
                      ('title', 'actor', 'location', 'kind', 'conditions', 'audience', 'interrupt')}
            update['expected_revision'] = old['revision']
            # The engine context conditions use equals, the transport equals_json.
            update['conditions'] = [{'path': c['path'], 'equals_json': json.dumps(c['equals'], ensure_ascii=False)}
                                    for c in old['conditions']]
        else:
            if creation is None:
                raise ValueError('New deadline without a creation definition.')
            update = deepcopy(creation)
            update['expected_revision'] = -1
        for key in ('entry_id', 'status', 'reason', 'references', 'outcome_event_index'):
            update[key] = deepcopy(command[key])
        needs_date = old is None or command['status'] == 'postponed'
        if needs_date:
            if command['due_minute'] is None:
                raise ValueError('A creation or a postponement requires an explicit future date.')
            update['due_minute'] = command['due_minute']
        else:
            if command['due_minute'] is not None:
                raise ValueError('The existing date belongs to the engine; use null outside a postponement. Entry: ' + command['entry_id'] + '; status : ' + command['status'] + '.')
            update['due_minute'] = old['due_minute']
        output.append(update)
    return output


INSTRUCTIONS = load_prompt('agenda_protocol.instructions')
