"""Deterministic health, mana and optional charge operations.

Charges (character_rules.charges) and cooldowns (abilities.<id>.cooldown) come from the configuration.
An ability with `effect: death_prevention` arms the neutralization of a lethal blow (prevent_death operation).
"""
from copy import deepcopy
from decimal import Decimal, ROUND_CEILING
import math
import re

from .magic import spell_cost
from .character import charge_resources, charges, solar_form
from .validation import validate

VERSION='mana-1.0'
BASE_PROTECTED={'player_hp','player_hp_max','player_mana','player_mana_max','night_id','player_combat_id','player_combat_active',
    'player_prevention_until_second','player_death_refusal_capacity_hp','player_prevention_survival_hp'}


def protected(rules):
    return BASE_PROTECTED|charge_resources(rules)


def _reset(resources,rules,period):
    for resource,charge in charges(rules).items():
        if charge['period']==period:resources[resource]=charge['max']


def _consumer(rules,operation):
    return next((resource for resource,charge in charges(rules).items() if charge.get('consumed_by')==operation),None)


def _require_charge_phase(charge, at, rules):
    phase = charge.get('phase', 'always')
    if phase == 'always':
        return
    schedule = rules.get('solar_schedule')
    if not schedule or solar_form(at // 60, schedule) != phase:
        raise ValueError('Charge unavailable in this form: ' + phase)


def operation_schema():
    fields={
        'kind':{'type':'string','enum':['ability','damage','elapsed','heal','restore_mana','combat_start','combat_end','resurrection','prevent_death']},
        'at_second':{'type':'integer','minimum':0},
        'ability_id':{'type':['string','null']},
        'output_percent':{'type':['number','null'],'minimum':0,'maximum':100},
        'amount':{'type':['integer','null'],'minimum':1},
        'start_second':{'type':['integer','null'],'minimum':0},
        'source_reference':{'type':'string','minLength':1}}
    return {'type':'array','maxItems':32,'items':{'type':'object','properties':fields,
        'required':list(fields),'additionalProperties':False}}


def instant(state):
    return state['world_minute']*60+state.get('world_second',0)


def validate_narration(proposal, config):
    """Reject explicit English casts of a declared ability when its operation was omitted."""
    if config.get('mechanics', {}).get('version') != VERSION:
        return
    from .narrative_coherence import assertions
    from .possessions import _plain, _positive
    actor = r'(?:you|' + re.escape(_plain(config.get('player_name') or 'player')) + ')'
    verbs = r'(?:cast|casts|activate|activates|use|uses|invoke|invokes|unleash|unleashes|channel|channels)'
    clauses = [_positive(clause) for clause in assertions(proposal['narration'])]
    for key, spec in config.get('character_rules', {}).get('abilities', {}).items():
        names = {key.replace('_', ' '), spec.get('label') or key}
        names.update(spec.get('catalogue', {}).get('aliases', []))
        name = '(?:' + '|'.join(re.escape(_plain(n)) for n in names) + ')'
        pattern = rf'\b{actor}\s+{verbs}\s+(?:(?:the|your)\s+)?{name}\b'
        declared = sum(len(re.findall(pattern, clause)) for clause in clauses)
        operations = sum(op['kind'] == 'ability' and op['ability_id'] == key
                         for event in proposal['events'] for op in event.get('mechanical_operations', []))
        if declared > operations:
            raise ValueError('Ability use without a mechanical operation: ' + key)


def _advance(resources, start, end, rules):
    """Advance only declared charge clocks. Passing time never heals the character."""
    schedule = rules.get('solar_schedule')
    if not schedule:
        return
    sunset = schedule['first_sunset_minute'] * 60
    first = max(0, (start - sunset) // 86400 + 1)
    last = (end - sunset) // 86400
    crossed = max(0, last - first + 1)
    if crossed:
        resources['night_id'] += crossed
        _reset(resources, rules, 'night')


def materialize(state,event,config):
    operations=event.get('mechanical_operations',[])
    validate(operation_schema(),operations)
    mode=config.get('mechanics')
    if not mode:
        if operations:raise ValueError('Deterministic mechanics not installed.')
        return deepcopy(event)
    if mode.get('version')!=VERSION:raise ValueError('Unsupported mechanics version. Create a new campaign with mana rules; existing saves are not converted automatically.')
    blocked=protected(config['character_rules'])
    for change in event['changes']:
        if change['path'] in {'/resources/'+key for key in blocked}:
            raise ValueError('Free mechanical mutation forbidden: '+change['path'])
    current=instant(state);end=event['world_minute']*60+event.get('world_second',0)
    if end<current:raise ValueError('Clock in seconds in the past.')
    rules=config['character_rules'];resources=deepcopy(state['resources'])
    for op in operations:
        at=op['at_second'];kind=op['kind']
        used={'ability':{'ability_id','output_percent'},'damage':{'amount'},
              'prevent_death':{'amount'},'heal':{'amount'},'restore_mana':{'amount'}}.get(kind,set())
        if any(op[key] is not None for key in ('ability_id','output_percent','amount','start_second') if key not in used):
            raise ValueError('Mechanical field without purpose; use null.')
        if not current<=at<=end:raise ValueError('Operations out of order or outside the interval.')
        if op['source_reference'] not in event['causes']:
            raise ValueError("Mechanical cause missing from the event's causes.")
        if op['start_second'] is not None:
            raise ValueError('start_second must be null.')
        _advance(resources,current,at,rules)
        current=at
        if kind=='ability':
            key=op['ability_id'];spec=rules.get('abilities', {}).get(key)
            if spec is None:raise ValueError('Unknown ability: '+str(key))
            if op['output_percent'] is not None and not 0 < op['output_percent'] <= 100:
                raise ValueError('Power must be above zero and at most 100%.')
            if spec.get('phase', 'always') != 'always' and spec['phase'] != solar_form(at//60,rules['solar_schedule']):
                raise ValueError('Ability unavailable in this form.')
            if resources['player_hp']==0:raise ValueError('Ability after a death.')
            cooldown=spec.get('cooldown')
            if cooldown:
                resource=cooldown['resource']
                if resource not in resources or at<resources[resource]:
                    raise ValueError('Ability on cooldown: '+key)
                resources[resource]=at+cooldown['seconds']
            costs = config.get('spell_cost_rules') or {}
            if key in costs.get('full_power_cost_percent', {}):
                cost = spell_cost(key,resources['player_mana_max'],costs,op['output_percent'])
            elif spec.get('free'):
                cost = 0
            else:
                raise ValueError('Mana cost not configured: ' + key)
            if cost > resources['player_mana']:
                raise ValueError('Insufficient mana.')
            for charge,declared in charges(rules).items():
                if declared.get('ability')==key and declared.get('consumed_by','ability')=='ability':
                    _require_charge_phase(declared, at, rules)
                    if not resources.get(charge):raise ValueError('Charge unavailable: '+key)
                    resources[charge]-=1
            resources['player_mana']-=cost
            if spec.get('effect')=='death_prevention':
                from .character import scaled_parameters
                parameters=scaled_parameters(spec,op['output_percent'] if op['output_percent'] is not None else costs.get('default_output_percent', 100))
                resources['player_prevention_until_second']=at+math.ceil(parameters['duration_seconds'])
                maximum = Decimal(resources['player_hp_max'])
                resources['player_death_refusal_capacity_hp']=int(maximum*Decimal(str(parameters['lethal_prevention_capacity_hp_max_percent']))/100)
                resources['player_prevention_survival_hp']=int((maximum*Decimal(str(parameters['survival_hp_max_percent']))/100).to_integral_value(rounding=ROUND_CEILING))
        elif kind=='damage':
            if type(op['amount']) is not int or op['amount']<=0:raise ValueError('Positive damage required.')
            resources['player_hp']=max(0,resources['player_hp']-op['amount'])
        elif kind=='heal':
            if resources['player_hp'] == 0:
                raise ValueError('Healing cannot revive a dead character.')
            if type(op['amount']) is not int or op['amount'] <= 0:
                raise ValueError('Positive healing required.')
            resources['player_hp'] = min(resources['player_hp_max'], resources['player_hp'] + op['amount'])
        elif kind=='restore_mana':
            if type(op['amount']) is not int or op['amount'] <= 0:
                raise ValueError('Positive mana recovery required.')
            resources['player_mana'] = min(resources['player_mana_max'], resources['player_mana'] + op['amount'])
        elif kind=='combat_start':
            if 'player_combat_active' not in resources:raise ValueError('Combat status to be established before the transition; no implicit refill.')
            if resources.get('player_combat_active'):raise ValueError('Combat already active; no refill.')
            resources['player_combat_active']=1;resources['player_combat_id']+=1
            _reset(resources,rules,'combat')
        elif kind=='combat_end':
            if 'player_combat_active' not in resources:raise ValueError('Unknown combat status.')
            if not resources.get('player_combat_active'):raise ValueError('No active combat.')
            resources['player_combat_active']=0
        elif kind=='resurrection':
            if resources['player_hp']!=0:raise ValueError('Resurrection without a death.')
            charge=_consumer(rules,'resurrection')
            if charge is None or not resources.get(charge):raise ValueError('Resurrection unavailable or already consumed.')
            _require_charge_phase(charges(rules)[charge], at, rules)
            resources[charge]-=1
            percent = rules.get('resurrection_hp_percent')
            if type(percent) is not int or not 1 <= percent <= 100:
                raise ValueError('Resurrection requires an explicit recovery percentage.')
            resources['player_hp']=(resources['player_hp_max'] * percent + 99)//100
        elif kind=='prevent_death':
            charge=_consumer(rules,'prevent_death')
            if (charge is None or not resources.get(charge) or
                not resources.get('player_combat_active') or
                at>=resources.get('player_prevention_until_second',0)):
                raise ValueError('Lethal prevention unavailable or expired.')
            _require_charge_phase(charges(rules)[charge], at, rules)
            if type(op['amount']) is not int or not 0<resources['player_hp']<=op['amount']<=resources.get('player_death_refusal_capacity_hp',0):
                raise ValueError('Non-lethal blow or exceeding the confirmed neutralization capacity.')
            survival=resources.get('player_prevention_survival_hp')
            if type(survival) is not int or not 0 < survival <= resources['player_hp_max']:
                raise ValueError('Active death-prevention recovery is missing; activate the ability again.')
            resources['player_hp']=survival
            resources[charge]-=1
    _advance(resources,current,end,rules)
    output=deepcopy(event)
    output['changes'].extend({'operation':'set','path':'/resources/'+key,'value':value}
        for key,value in resources.items() if key in blocked and state['resources'].get(key)!=value)
    return output
