"""Local vitality rules and safeguards for the abilities of the player character.

character_rules.charges ties each charge resource to its period (`night` or `combat`),
optionally to the ability (`ability`) or the operation (`consumed_by`) that consumes it.
character_rules.forbidden_resources lists resources the story may never create (for example a
gauge the campaign removed).
"""
from copy import deepcopy
from decimal import Decimal


def solar_form(minute, schedule):
    sunrise = schedule['first_sunrise_minute']
    sunset = schedule['first_sunset_minute']
    if minute < sunrise:
        return 'night'
    return 'day' if (minute - sunrise) % 1440 < sunset - sunrise else 'night'


def crossed_sunset(start, end, schedule):
    first = schedule['first_sunset_minute']
    return end >= first and (end - first) // 1440 > (start - first) // 1440


def charges(rules):
    """Declared charges: {resource: {'period': 'night'|'combat', 'max': 1, ...}}."""
    return {key: {'max': 1, **value} for key, value in (rules or {}).get('charges', {}).items()}


def charge_resources(rules):
    """Charge and cooldown resources, computed by the engine and not by a free-form story."""
    cooldowns = {spec['cooldown']['resource'] for spec in (rules or {}).get('abilities', {}).values()
                 if spec.get('cooldown')}
    return set(charges(rules)) | cooldowns


def scaled_parameters(spec, output):
    factor = Decimal(str(output)) / 100
    if not 0 < factor <= 1:
        raise ValueError('Invalid power.')
    result = deepcopy(spec.get('parameters', {}))
    for key, mode in spec.get('scaling', {}).items():
        full = Decimal(str(result[key]))
        if mode == 'multiplier':
            value = 1 + (full - 1) * factor
        elif mode == 'count':
            value = max(1, int(full * factor))
        else:
            value = full * factor
        result[key] = int(value) if value == int(value) else float(value)
    return result


def ability_context(state, rules, costs):
    """Abilities and vitality facts given to the narrator. A pack may declare only some rules: no day/night
    schedule or cost table are needed, and then those facts are simply absent."""
    maximum = state['resources']['player_hp_max']
    current = state['resources']['player_hp']
    schedule = rules.get('solar_schedule')
    phase = solar_form(state['world_minute'], schedule) if schedule else None
    default = (costs or {}).get('default_output_percent', 100)
    abilities = rules.get('abilities', {})
    context = {'current_hp': current, 'max_hp': maximum,
        'health_pct': current * 100 / maximum,
        'current_mana': state['resources'].get('player_mana', 0),
        'max_mana': state['resources'].get('player_mana_max', 0),
        'active_abilities': [key for key, value in abilities.items()
                             if phase is None or value.get('phase', 'always') in (phase, 'always')],
        'default_power_effects': {key: scaled_parameters(value, default)
                                  for key, value in abilities.items()
                                  if value.get('parameters')},
        'default_power_pct': default,
        'combat_tracking': {key: value for key, value in state['resources'].items()
                        if key in {'player_combat_id', 'night_id', *charges(rules)}}}
    if rules.get('version'):
        context['version'] = rules['version']
    if phase:
        context['form'] = phase
    return context


def validate_character_transition(before, after, config):
    from .cooldowns import validate_transition
    validate_transition(before, after, config)
    rules = config.get('character_rules')
    if not rules:
        return
    old, new = before['resources'], after['resources']
    declared = charges(rules)
    periods = {charge['period'] for charge in declared.values()}
    required = ('player_hp', 'player_hp_max', *(('night_id',) if 'night' in periods else ()),
                *(('player_combat_id',) if 'combat' in periods else ()), *declared)
    for key in required:
        if key not in new or type(new[key]) is not int or new[key] < 0:
            raise ValueError('Invalid mandatory character resource: ' + key)
    for key in rules.get('forbidden_resources', []):
        if key in new:
            raise ValueError('This resource is forbidden for the character: ' + key)
    if 'player_mana_max' in old or config.get('mechanics', {}).get('version') == 'mana-1.0':
        for key in ('player_mana', 'player_mana_max'):
            if type(new.get(key)) is not int or new[key] < 0:
                raise ValueError('Invalid mandatory mana resource: ' + key)
        if new['player_mana_max'] != rules.get('mana_max', old.get('player_mana_max')):
            raise ValueError('The maximum mana cannot change during a turn.')
        if new['player_mana'] > new['player_mana_max']:
            raise ValueError('Mana exceeds the character maximum.')
    if new['player_hp_max'] != rules.get('hp_max', old.get('player_hp_max', new['player_hp_max'])):
        raise ValueError('The confirmed maximum HP of the character cannot change during a turn.')
    if not 0 <= new['player_hp'] <= new['player_hp_max']:
        raise ValueError("The character's HP exceed its maximum vitality.")
    if new.get('night_id', 0) < old.get('night_id', 0):
        raise ValueError('The night identifier cannot go backwards.')
    for key in (key for key, charge in declared.items() if charge['period'] == 'night'):
        if not 0 <= new.get(key, 0) <= declared[key]['max']:
            raise ValueError('A night charge cannot accumulate: ' + key)
        if key in old and new.get(key, 0) > old[key]:
            if (new.get('night_id', 0) <= old.get('night_id', 0)
                    or not crossed_sunset(before['world_minute'], after['world_minute'], rules['solar_schedule'])):
                raise ValueError('Night charge refilled without a new fictional night: ' + key)
    if new.get('player_combat_id', 0) < old.get('player_combat_id', 0):
        raise ValueError('The combat identifier cannot go backwards.')
    for key in (key for key, charge in declared.items() if charge['period'] == 'combat'):
        if not 0 <= new.get(key, 0) <= declared[key]['max']:
            raise ValueError('Invalid combat charge: ' + key)
        if key in old and new.get(key, 0) > old[key] and new.get('player_combat_id', 0) <= old.get('player_combat_id', 0):
            raise ValueError('A combat charge is not refilled within the same combat: ' + key)
