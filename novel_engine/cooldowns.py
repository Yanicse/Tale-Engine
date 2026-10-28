"""Persistent ability cooldowns, even in a campaign without deterministic mechanics.

An ability declares `cooldown: {"resource": ..., "seconds": ...}` in character_rules.abilities;
the resource holds the absolute fictional second from which it becomes available again.
"""


def instant(state):
    return state['world_minute'] * 60 + state.get('world_second', 0)


def cooldowns(config):
    abilities = config.get('character_rules', {}).get('abilities', {})
    return {key: (spec['cooldown']['resource'], spec['cooldown']['seconds'])
            for key, spec in abilities.items() if spec.get('cooldown')}


def validate_event(before, event, config):
    for ability, (resource, cooldown) in cooldowns(config).items():
        changes = [c for c in event['changes'] if c['path'] == '/resources/' + resource]
        if not changes or resource not in before['resources']:
            continue
        if len(changes) != 1 or changes[0]['operation'] != 'set':
            raise ValueError(f'An activation of {ability} sets a single absolute deadline.')
        at = event['world_minute'] * 60 + event.get('world_second', 0)
        old, new = before['resources'][resource], changes[0]['value']
        if old > at or type(new) is not int or new <= old:
            raise ValueError(f'{ability} still unavailable or cooldown cut short.')
        if not config.get('mechanics') and new != at + cooldown:
            raise ValueError(f'Wrong deadline for {ability}: activation + {cooldown} fictional seconds expected.')


def validate_transition(before, after, config):
    for ability, (resource, cooldown) in cooldowns(config).items():
        old = before['resources'].get(resource)
        new = after['resources'].get(resource)
        if type(new) is not int or new < 0:
            raise ValueError(f'The persistent deadline of {ability} is required and must be positive or zero.')
        if old is None:
            if new != 0:
                raise ValueError(f'Installation of {ability} without an expected activation.')
            continue
        if new == old:
            continue
        if new < old:
            raise ValueError(f'Early cooldown reset of {ability} is forbidden.')
        start, end = instant(before), instant(after)
        if old > end or not max(start, old) + cooldown <= new <= end + cooldown:
            raise ValueError(f'{ability} unavailable or wrong cooldown deadline.')
        if not config.get('mechanics') and new != end + cooldown:
            raise ValueError(f'{ability} must set its deadline at the minute of its activation + {cooldown} seconds.')
