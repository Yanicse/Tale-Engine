"""Public lookup of the powers and costs actually installed in SQLite.

Labels, forms and free uses come from character_rules (`passives` and `abilities`:
`label`, `phase`, `free`); charges come from character_rules.charges.
"""
from .magic import spell_cost
from .character import charges


def packet(state, config, recipient='player', query=''):
    rules = config.get('spell_cost_rules', {})
    costs = rules.get('full_power_cost_percent', {})
    character = config.get('character_rules', {})
    catalog = {**character.get('passives', {}), **character.get('abilities', {})}
    by_ability = {charge['ability']: (resource, charge['period'])
                  for resource, charge in charges(character).items() if charge.get('ability')}
    resources = state['resources']
    maximum = resources.get('player_mana_max', 0)
    default = rules.get('default_output_percent', 100)
    abilities = []
    for key, power in state['powers'].items():
        if power['actor_id'] != config.get('player_id', 'player') or recipient not in power['audience']:
            continue
        info = catalog.get(key, {})
        name = info.get('label') or power['description'].split(' : ', 1)[0]
        phase = info.get('phase', 'passive' if key in character.get('passives', {}) else 'unspecified')
        if query and query.casefold() not in (key + ' ' + name + ' ' + power['description']).casefold():
            continue
        cost = spell_cost(key, maximum, rules) if key in costs else (0 if info.get('free') else None)
        full = spell_cost(key, maximum, rules, 100) if key in costs else cost
        item = {'id': key, 'name': name, 'form': phase,
                'description': power['description'], 'limits': power['limits'],
                'default_mana_cost': cost, 'full_power_mana_cost': full,
                'max_cost_pct': costs.get(key),
                'free_up_to_power_pct': rules.get('free_output_up_to_percent', {}).get(key), 'charge': None}
        if key in by_ability:
            resource, period = by_ability[key]
            item['charge'] = {'left': resources.get(resource), 'period': period}
        if info.get('cooldown'):
            cooldown = info['cooldown']
            now = state['world_minute'] * 60 + state.get('world_second', 0)
            ready = resources.get(cooldown['resource'])
            item['cooldown'] = {'available_at_second': ready,
                'seconds_left': max(0, ready - now) if ready is not None else None}
        abilities.append(item)
    return {'status': 'skills', 'character': config.get('player_name') or 'Character', 'hp': resources['player_hp'], 'hp_max': resources['player_hp_max'],
            'mana': resources.get('player_mana', 0), 'mana_max': maximum,
            'default_power_pct': default, 'abilities': abilities}


def render(result):
    lines = [f"{result.get('character', 'Character')}: {result['hp']}/{result['hp_max']} HP. Mana: {result.get('mana', 0)}/{result.get('mana_max', 0)}. Default power: {result['default_power_pct']}%.",
             'Costs are paid once, at casting. Effects also shrink with the dosage.']
    for item in result['abilities']:
        cost = item['default_mana_cost']
        label = ('Cost: see the detailed abilities or the limits below.' if cost is None else
                 ('Cost: 0 mana.' if cost == 0 else
                  f"Cost: {cost} mana at the default dosage; {item['full_power_mana_cost']} mana at 100% ({item['max_cost_pct']}% of maximum mana)."))
        if item.get('free_up_to_power_pct') is not None:
            label = (f"Cost: free up to {item['free_up_to_power_pct']}% power inclusive; "
                     f"beyond that, cost is proportional to the dosage. At 100%: {item['full_power_mana_cost']} mana "
                     f"({item['max_cost_pct']}% of maximum mana). At the default dosage: {cost} mana.")
        lines.extend(['', item['name'] + ' (' + item['form'] + ')', label,
                      item['description'], 'Limits: ' + item['limits']])
        if item['charge']:
            charge = item['charge']
            remaining = 'unknown' if charge['left'] is None else str(charge['left'])
            lines.append('Charges left: ' + remaining + ' (period: ' + charge['period'] + ').')
        if item.get('cooldown'):
            remaining = item['cooldown']['seconds_left']
            lines.append('Cooldown: ' + ('unknown.' if remaining is None else
                ('available.' if remaining == 0 else str(remaining) + ' fictional seconds left.')))
    if not result['abilities']:
        lines.append('No accessible ability found.')
    return '\n'.join(lines)
