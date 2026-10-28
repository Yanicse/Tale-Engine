"""Local CLI for tests: no network access, answers only to the packet on stdin."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from novel_engine.validation import validate


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    role, control = sys.argv[1:3]
    args = sys.argv[3:]
    assert 'service_tier="standard"' in args
    schema = json.loads(Path(args[args.index('--output-schema')+1]).read_text(encoding='utf-8'))
    # Check independent of the compiler: each sub-schema is typed and closed.
    def strict(node):
        assert 'type' in node, node
        if 'properties' in node:
            assert set(node['required']) == set(node['properties'])
            assert node['additionalProperties'] is False
            for child in node['properties'].values(): strict(child)
        if 'items' in node: strict(node['items'])
    strict(schema)
    prompt = sys.stdin.buffer.read().decode('utf-8')
    marker = ('\n'
              'WORLD REVIEW\n') if role == 'planner' else '\nCURRENT TURN\n'
    packet = json.loads(prompt.rsplit(marker, 1)[1])
    control = Path(control)
    mode = control.read_text() if control.exists() else ''
    with control.with_suffix('.jsonl').open('a', encoding='utf-8') as log:
        log.write(json.dumps({'role':role, 'state':packet['current_state']['state_version'],
                              'mode':mode, 'schema_bytes':len(json.dumps(schema))})+'\n')
    if mode == 'hang':
        import time; time.sleep(30)
    if mode == 'outage':
        control.write_text('')
        print(json.dumps({'type':'turn.failed','error':{'message':'HTTP 503 unavailable'}}))
        return 1
    if role == 'planner':
        exchanges = packet['exchanges_to_summarize']; blocks = packet['summaries_to_consolidate']
        response = {'planner_schema_version':'2.0', 'chronicle':None,
                    'player_summary':{'text':'The recorded steps are accomplished.',
                        'references':[e['reference'] for e in exchanges]} if exchanges else None,
                    'old_synthesis':{'text':'The periods are synthesized with their caveats.',
                        'references':[e['reference'] for e in blocks]} if blocks else None}
        if 'chronicle' in packet:
            # A nearly full clock bursts into an interrupting visit, otherwise a simple sign.
            clock = packet['chronicle']['clocks'][0]
            item = {k:v for k,v in clock.items() if k not in ('revision','reference')}
            item.update(expected_revision=clock['revision'], progression=clock['progression']+1,
                        reason='Time passes.', references=[clock['reference']])
            burst = item['progression'] == item['segments']
            if burst: item['status'] = 'burst'
            response['chronicle'] = {'clocks':[item], 'manifestations':[{'id':'test_visit',
                'clock':clock['id'], 'type':'event' if burst else 'sign', 'title':'Visite',
                'actor':'messenger', 'place':'gate', 'description':'A messenger brings news.',
                'perception':'The messenger arrives at the door.', 'due_minute':packet['current_state']['world_minute']+3}]}
            registry = packet['chronicle'].get('arcs_registry')
            if registry is not None:
                now = packet['current_state']['world_minute']
                response['chronicle']['arcs'] = [{'id':a['id'],'expected_revision':a['revision'],'status':'in_progress',
                    'step':min(a['step']+1,len(a['steps'])),'motif':'progression',
                    'reason':'The arc moves forward off screen.','world_fact':'Off screen, the situation develops.',
                    'next_review':now+1440,'references':[a['reference']]}
                    for a in registry['arcs'] if a['id'] in registry['to_process']]
    else:
        now = packet['current_state']['world_minute']
        response = {'narrative_schema_version':'1.2', 'narration':'You complete this step.',
                    'events':[], 'blocking_questions':[], 'memory_request':None,
                    'memory_updates':[], 'agenda_updates':[], 'continuation':None,'roll_checks':[]}
        if mode == 'memory':
            control.write_text('')
            response['memory_request'] = {'query':'step', 'reason':'Find a recorded step again.'}
        else:
            end = now + 2
            due = [e for e in packet['world_agenda']['events'] if e['due_minute'] <= end]
            if due:
                entry = due[0]; end = max(now, entry['due_minute'])
                response['agenda_updates'] = [{'entry_id':entry['entry_id'], 'status':'finished',
                    'reason':'The messenger has arrived.', 'references':[entry['reference']],
                    'outcome_event_index':0,'due_minute':None,'creation':None}]
                causes = [entry['reference'], 'current_action']
                if entry['interrupt']:
                    response['continuation'] = {'goal':packet['player_input'],
                        'remaining':'What follows awaits your decision.', 'status':'suspended','event_index':0}
            else: causes = ['current_action']
            response['events'] = [{'world_minute':end,'description':'Step recorded.',
                'causes':causes, 'conditions':[], 'changes':[], 'audience':['narrator','player'], 'asset_operations':[]}]
            response['memory_updates'] = [{'topic_id':'test_steps','title':'Steps',
                                           'aliases':['step'],'references':['current_turn']}]
            if mode == 'invalid_reference':
                response['events'][0]['causes'] = ['missing_reference']
                control.write_text('')
                # Simulate an output that does not respect the contract to test the local validation.
                schema = None
        if packet['campaign_rules'].get('mechanics'):
            response['narrative_schema_version']='1.3'
            for e in response['events']:
                e.update(world_second=0,mechanical_operations=[],public_consequences=[])
            if mode.startswith('site_'):
                state=packet['current_state'];second=state.get('world_second',0)
                e={'world_minute':now,'world_second':second,'description':'Fictional step recorded.',
                    'causes':['current_action'],'conditions':[],'changes':[],'audience':['narrator','player'],
                    'asset_operations':[],'mechanical_operations':[],'public_consequences':[]}
                response.update(events=[e],agenda_updates=[],continuation=None,memory_updates=[])
                def claim(kind,path,value):return {'kind':kind,'path':path,'value_json':json.dumps(value),'source_reference':'current_action'}
                if mode=='site_dialogue':response['events']=[]
                elif mode=='site_arrival':
                    e.update(world_minute=now+1,world_second=0)
                    e['changes']=[{'operation':'set','path':'/positions/player','value_json':json.dumps('market')}]
                    e['public_consequences']=[claim('arrival','/positions/player','market')]
                elif mode=='site_purchase':
                    item={'owner_id':'player','quantity':1,'equipped':False,'description':'Bois fictional.',
                        'references':['current_action'],'audience':['narrator','player'],'location':'on:player',
                        'condition':'Sain','quantity_precision':'exact','possession_status':'owns','unit':'coin'}
                    e['asset_operations']=[{'kind':'purchase','money_resource':'player_silver','money_delta':-3,
                        'inputs':[],'outputs':[{'item_id':'wood','item_json':json.dumps(item)}],'destination':None}]
                    e['public_consequences']=[claim('payment','/resources/player_silver',state['resources']['player_silver']-3)]
                elif mode=='site_power':
                    e['mechanical_operations']=[{'kind':'ability','at_second':now*60+second,
                        'ability_id':'burning_blood','output_percent':10,'amount':None,'start_second':None,'source_reference':'current_action'}]
                    e['public_consequences']=[claim('damage','/resources/player_hp',state['resources']['player_hp']-150)]
                elif mode in ('site_spell','site_overspend','site_damage','site_heal','site_restore'):
                    res=state['resources']
                    def op(kind,**kw):
                        d={'kind':kind,'at_second':now*60+second,'ability_id':None,'output_percent':None,'amount':None,
                           'start_second':None,'source_reference':'current_action'}
                        d.update(kw);return d
                    if mode=='site_spell':
                        e['mechanical_operations']=[op('ability',ability_id='second_wind')]
                        e['public_consequences']=[claim('mana','/resources/player_mana',res['player_mana']-9)]
                    elif mode=='site_overspend':
                        e['mechanical_operations']=[op('ability',ability_id='second_wind') for _ in range(4)]
                        e['public_consequences']=[claim('mana','/resources/player_mana',res['player_mana']-36)]
                    elif mode=='site_damage':
                        e['mechanical_operations']=[op('damage',amount=5)]
                        e['public_consequences']=[claim('damage','/resources/player_hp',res['player_hp']-5)]
                    elif mode=='site_heal':
                        e['mechanical_operations']=[op('heal',amount=3)]
                        e['public_consequences']=[claim('healing','/resources/player_hp',min(res['player_hp_max'],res['player_hp']+3))]
                    elif mode=='site_restore':
                        e['mechanical_operations']=[op('restore_mana',amount=4)]
                        e['public_consequences']=[claim('mana','/resources/player_mana',min(res['player_mana_max'],res['player_mana']+4))]
                elif mode=='site_recovery':e.update(world_minute=now+1,world_second=0)
                elif mode=='site_due':
                    due=[entry for entry in packet['world_agenda']['events'] if entry['due_minute']<=now+2]
                    first=min(entry['due_minute'] for entry in due)
                    due=[entry for entry in due if entry['due_minute']==first]
                    response['events']=[]
                    for index,entry in enumerate(due):
                        outcome={**e,'world_minute':max(now,first),'world_second':0,
                            'causes':[entry['reference'],'current_action'],
                            'audience':['narrator','player'] if entry['interrupt'] else entry['audience']}
                        response['events'].append(outcome)
                        response['agenda_updates'].append({'entry_id':entry['entry_id'],'status':'finished','reason':'Fictional check completed.',
                            'references':[entry['reference']],'outcome_event_index':index,'due_minute':None,'creation':None})
                        if entry['interrupt']:
                            response['continuation']={'goal':packet['player_input'],'remaining':'Decision awaited.',
                                'status':'suspended','event_index':index}
    if role=='narrator' and packet['campaign_rules'].get('creation_format')=='1':
        response.update(creation_schema_version='1',creation_updates=[])
        if 'creation' in mode:
            response.update(events=[],agenda_updates=[],continuation=None,memory_updates=[])
            if mode in ('npc_creation','offer_creation','creation_accept'):
                quest=mode!='npc_creation'
                response['creation_updates']=[{'entity_id':'lina_mission' if quest else 'lina',
                    'name':'Bridge mission' if quest else 'Lina','aliases':['Pont old'] if quest else ['Pisteuse'],
                    'kind':'quest' if quest else 'npc','origin':'campaign',
                    'status':('accepted' if mode=='creation_accept' else 'proposed') if quest else 'established',
                    'traits':['The mission requires a report before any bounty.'] if quest else ['Lina knows the trails of the town.'],
                    'unknowns':['Level unknown.'],'links':['market'],'audience':['narrator','player'],
                    'references':['current_turn']}]
                response['narration']='The mission awaits your acceptance; its bounty requires a report.' if mode=='offer_creation' else 'Lina, the Tracker, knows the trails of the town.'
            elif mode=='reminder_creation':
                assert any(v['entity_id']=='lina' for v in packet['campaign_creations'])
                assert any(v['entity_id']=='lina_mission' for v in packet['campaign_creations'])
                response['narration']='Lina knows the trails of the town. The mission requires a report before any bounty.'
    if schema is not None: validate(schema, response)
    print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(response)}}))
    print(json.dumps({'type':'turn.completed','usage':{'input_tokens':0,'output_tokens':0}}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
