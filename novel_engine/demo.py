"""Deterministic fictional scene to exercise the mechanics, with no generation."""
from copy import deepcopy
import json
import uuid

from .contracts import ASSETS


def initial_state(campaign_id='demo'):
    old = json.loads((ASSETS/'docs/engine/examples/state_0.json').read_text(encoding='utf-8'))
    state = deepcopy(old)
    state.update(schema_version='0.2',campaign_id=campaign_id,commitments={},beliefs={},inventory={},effects={},relations={},powers={})
    state['versions'] = {'canon_revision':102,'canon_snapshot':'canon/snapshots/canon_revision_102.sqlite','adaptation_version':'prototype-0.1'}
    return state


def configuration():
    rules = json.loads((ASSETS/'adaptation/prototype_rules.json').read_text(encoding='utf-8'))
    return {'fictional':True,'canon_access':{},'rule_ids':[r['id'] for r in rules['rules']],
            'model_adapter':'disabled','reading_budget':{'max_tasks':4,'max_calls':0},
            'resource_owners':{'player_gold':'player'},
            'seed_references':['demo:arrival_event','demo:initial_configuration']}


def proposal(state, narration='', events=None, request_id=None, questions=None):
    return {'schema_version':'0.2','request_id':request_id or uuid.uuid4().hex,
            'campaign_id':state['campaign_id'],'expected_state_version':state['state_version'],
            'narration':narration,'events':events or [],'blocking_questions':questions or []}


def event(state, description, changes, minute=None, audience=None, conditions=None, causes=None):
    return {'event_id':uuid.uuid4().hex,'world_minute':state['world_minute'] if minute is None else minute,
            'description':description,'causes':causes or ['demo:initial_configuration'],
            'conditions':conditions or [],'changes':changes,'audience':audience or ['narrator','player']}


def action(state, text):
    command = text.strip().casefold()
    if command in ('depart','departure','leave'):
        if state['positions']['player']=='route':
            return proposal(state,questions=['You are already on the road. Which action do you want to try?'])
        departure = event(state,'You pass through the gates and take the road.',
                          [{'operation':'set','path':'/positions/player','value':'route'}],
                          minute=state['world_minute']+2,causes=['adaptation:movement_demo'])
        events = [departure]
        if state['positions'].get('lookout')=='town_gate':
            observation = event(state,'The lookout sees the departure and informs their faction.',
                [{'operation':'set','path':'/knowledge/faction','value':state['knowledge'].get('faction',[])+[departure['event_id']]}],
                minute=departure['world_minute'],audience=['narrator','npc:faction'],causes=[departure['event_id']])
            events.append(observation)
        return proposal(state,departure['description'],events)
    if command.startswith('wait'):
        parts = command.split()
        minutes = int(parts[1]) if len(parts)==2 and parts[1].isdigit() else 1
        if not 1<=minutes<=1440:
            return proposal(state,questions=['Choose a wait between one minute and one day.'])
        wait = event(state,f'You wait {minutes} minute(s).',
                     [{'operation':'set','path':'/positions/player','value':state['positions']['player']}],
                     minute=state['world_minute']+minutes,causes=['adaptation:wait_demo'])
        return proposal(state,wait['description'],[wait])
    return proposal(state,questions=[('This demo offers leave or wait. Free actions will be resolved by the narrator '
                                      'connected to the engine.')])


def task(state, goal='surveillance'):
    if goal=='expiration':
        allowed = ['/effects/'+key for key,value in state['effects'].items()
                   if value['expires_at'] is not None and value['expires_at'] <= state['world_minute']]
        if not allowed:
            return None
        actors = ['system']
        audience = ['narrator','player']
        for value in state['effects'].values():
            audience += value['audience']
    else:
        if state['intentions'].get('faction')=='have_follow_route' or (state['positions']['player']=='town_gate' and state['intentions'].get('faction')=='watch_gates'):
            return None
        allowed = ['/intentions/faction']
        actors = ['faction']
        audience = ['narrator','npc:faction']
    return {'schema_version':'0.2','task_id':uuid.uuid4().hex,'campaign_id':state['campaign_id'],
            'expected_state_version':state['state_version'],'kind':'autonomous','actor_ids':actors,
            'context_file':'generated','goal':goal,'not_before_world_minute':state['world_minute'],
            'conditions':[],'status':'pending','budget':{'max_model_calls':0,'max_seconds':2},
            'cancel_on_player_input':True,'allowed_paths':allowed,'allowed_audience':list(set(audience)),'handler':'script'}


def handle_background(spec,state,cancel):
    # Cooperative pause showing an interruptible computation during reading.
    if cancel.wait(0.15):
        return proposal(state,request_id=spec['task_id'])
    if spec['goal']=='expiration':
        expired = [(key,value) for key,value in state['effects'].items()
                   if value['expires_at'] is not None and value['expires_at'] <= state['world_minute']]
        events = [event(state,'A temporary effect comes to an end.',
                        [{'operation':'remove','path':'/effects/'+key,'value':None}],
                        audience=value['audience'],causes=['adaptation:effects']) for key,value in expired]
        return proposal(state,events=events,request_id=spec['task_id'])
    observed_departure = any(ref!='demo:arrival_event' for ref in state['knowledge'].get('faction',[]))
    intention = 'have_follow_route' if observed_departure else 'watch_gates'
    condition = {'path':'/knowledge/faction','equals':state['knowledge'].get('faction',[])}
    description = 'The faction decides to have the traveler followed.' if observed_departure else 'The faction prepares surveillance of the gates.'
    plan = event(state,description,[{'operation':'set','path':'/intentions/faction','value':intention}],
                 audience=['narrator','npc:faction'],conditions=[condition],
                 causes=state['knowledge'].get('faction',[]) or ['demo:initial_configuration'])
    return proposal(state,events=[plan],request_id=spec['task_id'])
