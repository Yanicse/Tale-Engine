"""Vitality operations, explicitly enabled in the SQLite configuration.

Charges (character_rules.charges) and cooldowns (abilities.<id>.cooldown) come from the configuration.
An ability with `effect: death_prevention` arms the neutralization of a lethal blow (prevent_death operation).
"""
from copy import deepcopy
from fractions import Fraction

from .magic import spell_cost
from .character import charge_resources, charges, solar_form
from .validation import validate

VERSION='vitality-1.0'
BASE_PROTECTED={'player_hp','player_hp_max','night_id','player_combat_id','player_combat_active',
    'player_regen_rest_num','player_regen_rest_den',
    'player_prevention_until_second','player_death_refusal_capacity_hp'}


def protected(rules):
    return BASE_PROTECTED|charge_resources(rules)


def _reset(resources,rules,period):
    for resource,charge in charges(rules).items():
        if charge['period']==period:resources[resource]=charge['max']


def _consumer(rules,operation):
    return next((resource for resource,charge in charges(rules).items() if charge.get('consumed_by')==operation),None)


def operation_schema():
    fields={
        'kind':{'type':'string','enum':['ability','damage','elapsed','blood_interval','combat_start','combat_end','resurrection','prevent_death']},
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


def _advance(resources,start,end,rules,blood=False):
    schedule=rules['solar_schedule']
    boundaries={start,end}
    for day in range(start//86400-1,end//86400+2):
        for key in ('first_sunrise_minute','first_sunset_minute'):
            boundary=(schedule[key]+day*1440)*60
            if start<boundary<=end:boundaries.add(boundary)
    points=sorted(boundaries)
    rest=Fraction(resources.get('player_regen_rest_num',0),resources.get('player_regen_rest_den',1))
    for left,right in zip(points,points[1:]):
        phase=solar_form(left//60,schedule)
        if blood and phase!='night':raise ValueError('Confirmed blood regimen reserved to the vampiric form.')
        if resources['player_hp']>0:
            rate=rules['regeneration']['drunk_blood' if blood else phase]
            value=rest+Fraction(resources['player_hp_max'])*Fraction(str(rate['percent']))*(right-left)/(100*Fraction(str(rate['seconds'])))
            gain=value.numerator//value.denominator
            resources['player_hp']=min(resources['player_hp_max'],resources['player_hp']+gain)
            rest=value-gain if resources['player_hp']<resources['player_hp_max'] else Fraction(0)
        # Real solar boundary, with no retroactive recovery at installation.
        sunset=schedule['first_sunset_minute']*60
        if right>=sunset and (right-sunset)%86400==0 and right>left:
            resources['night_id']+=1
            _reset(resources,rules,'night')
    resources['player_regen_rest_num']=rest.numerator
    resources['player_regen_rest_den']=rest.denominator


def materialize(state,event,config):
    operations=event.get('mechanical_operations',[])
    validate(operation_schema(),operations)
    mode=config.get('mechanics')
    if not mode:
        if operations:raise ValueError('Deterministic mechanics not installed.')
        return deepcopy(event)
    if mode.get('version')!=VERSION:raise ValueError('Unknown mechanics version.')
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
              'prevent_death':{'amount'},'blood_interval':{'start_second'}}.get(kind,set())
        if any(op[key] is not None for key in ('ability_id','output_percent','amount','start_second') if key not in used):
            raise ValueError('Mechanical field without purpose; use null.')
        if not current<=at<=end:raise ValueError('Operations out of order or outside the interval.')
        if op['source_reference'] not in event['causes']:
            raise ValueError("Mechanical cause missing from the event's causes.")
        if kind=='blood_interval':
            if op['start_second']!=current or at<=current:
                raise ValueError('Blood intake or interval already used or not contiguous.')
            _advance(resources,current,at,rules,blood=True)
        else:
            if op['start_second'] is not None:raise ValueError('Interval reserved to blood actually drunk.')
            _advance(resources,current,at,rules)
        current=at
        if kind=='ability':
            key=op['ability_id'];spec=rules['abilities'].get(key)
            if spec is None:raise ValueError('Unknown ability: '+str(key))
            if spec['phase'] not in ('always',solar_form(at//60,rules['solar_schedule'])):
                raise ValueError('Ability unavailable in this form.')
            if resources['player_hp']==0:raise ValueError('Ability after a death.')
            cooldown=spec.get('cooldown')
            if cooldown:
                resource=cooldown['resource']
                if resource not in resources or at<resources[resource]:
                    raise ValueError('Ability on cooldown: '+key)
                resources[resource]=at+cooldown['seconds']
            cost=spell_cost(key,resources['player_hp_max'],config['spell_cost_rules'],op['output_percent'])
            if cost>resources['player_hp']:raise ValueError('Insufficient HP.')
            if cost and cost==resources['player_hp']:
                raise ValueError('Rule to be specified: paid cast reaching exactly zero HP.')
            for charge,declared in charges(rules).items():
                if declared.get('ability')==key and declared.get('consumed_by','ability')=='ability':
                    if not resources.get(charge):raise ValueError('Charge unavailable: '+key)
                    resources[charge]-=1
            resources['player_hp']-=cost
            if spec.get('effect')=='death_prevention':
                from .character import scaled_parameters
                parameters=scaled_parameters(spec,op['output_percent'] or config['spell_cost_rules']['default_output_percent'])
                resources['player_prevention_until_second']=at+parameters['duration_seconds']
                resources['player_death_refusal_capacity_hp']=int(resources['player_hp_max']*parameters['lethal_prevention_capacity_hp_max_percent']/100)
        elif kind=='damage':
            if type(op['amount']) is not int or op['amount']<=0:raise ValueError('Positive damage required.')
            resources['player_hp']=max(0,resources['player_hp']-op['amount'])
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
            phase=charges(rules)[charge].get('phase')
            if phase and solar_form(at//60,rules['solar_schedule'])!=phase:raise ValueError('Resurrection outside its form: '+phase)
            resources[charge]-=1
            resources['player_hp']=(resources['player_hp_max']+1)//2
        elif kind=='prevent_death':
            charge=_consumer(rules,'prevent_death')
            if (charge is None or not resources.get(charge) or
                not resources.get('player_combat_active') or
                at>=resources.get('player_prevention_until_second',0)):
                raise ValueError('Lethal prevention unavailable or expired.')
            if type(op['amount']) is not int or not 0<resources['player_hp']<=op['amount']<=resources.get('player_death_refusal_capacity_hp',0):
                raise ValueError('Non-lethal blow or exceeding the confirmed neutralization capacity.')
            spec=next(spec for spec in rules['abilities'].values() if spec.get('effect')=='death_prevention')
            survival=spec['parameters']['survival_hp_max_percent']
            resources['player_hp']=(resources['player_hp_max']*survival+99)//100
            resources[charge]-=1
    _advance(resources,current,end,rules)
    output=deepcopy(event)
    output['changes'].extend({'operation':'set','path':'/resources/'+key,'value':value}
        for key,value in resources.items() if key in blocked and state['resources'].get(key)!=value)
    return output
