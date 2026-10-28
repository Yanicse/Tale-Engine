"""Reliability regressions on an entirely fictional campaign."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from novel_engine.adapters import ModelUnavailable
from novel_engine.agenda import Agenda,RESPONSE_LIMIT
from novel_engine.campaign import Campaign
from novel_engine.contracts import check_json
from novel_engine.demo import initial_state,configuration,event,proposal
from novel_engine.indexed_memory import MemoryIndex
from novel_engine.mechanics import instant
from novel_engine.narrative import CONTEXT_BUDGET_BYTES, NarrativeLoop, packet_references
from novel_engine.planner import WorldPlanner,review_world
from test_planner import FakePlanner, install_test_chronicle


class FakeNarrator:
    """Offline narrator: a replaceable canned response, with an optional transform of the packet."""
    def __init__(self):
        self.calls=0;self.last_metrics={};self.transform=None
        self.response={'narration':'The guard answers your question.','events':[],'blocking_questions':[]}

    def generate(self,prompt):
        self.calls+=1
        response=deepcopy(self.response)
        if self.transform:
            return self.transform(json.loads(prompt.rsplit('\nCURRENT TURN\n',1)[1]),response)
        return response


def item(quantity=1,precision='exact'):
    return {'owner_id':'player','quantity':quantity,'equipped':False,'description':'A lot of wood.',
            'references':['demo:initial_configuration'],'audience':['narrator','player'],
            'location':'on:player','condition':'Sound','quantity_precision':precision,
            'possession_status':'owns','unit':'piece'}


def op(kind,delta=0,inputs=None,outputs=None,destination=None):
    return {'kind':kind,'money_resource':'player_silver' if delta else None,
            'money_delta':delta,'inputs':inputs or [],
            'outputs':[{'item_id':key,'item_json':check_json(value)} for key,value in (outputs or {}).items()],
            'destination':destination}


class Fixture(unittest.TestCase):
    def setUp(self):
        (ROOT/'.tmp-tests').mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'.tmp-tests')
        self.path=Path(self.temp.name)/'fictional.sqlite'
        self.c=Campaign(self.path).create(initial_state('reliability'),configuration())
        self.c.open_scene('The guard waits at the door.')
        self.n=FakeNarrator()

    def tearDown(self):self.temp.cleanup()

    def loop(self):return NarrativeLoop(Campaign(self.path),self.n)


class ReceiptTests(Fixture):

    def test_old_attempt_without_new_fields_or_without_receipt_is_readable(self):
        pending=self.c.begin_action('Hello')
        old=proposal(self.c.state(),'Fictional historical answer.',request_id=pending['request_id'])
        self.loop()._save_attempt(pending['request_id'],'prepare',old)
        self.assertEqual(self.loop().run_turn(resume=True)['status'],'recorded')
        self.assertEqual(self.n.calls,0)
        pending=self.c.begin_action('Next')
        self.loop()._save_attempt(pending['request_id'],'failure',reason='Old failure without a receipt.')
        with self.assertRaises(ModelUnavailable):self.loop().run_turn(resume=True)
        self.assertEqual(self.n.calls,0)

    def test_value_error_after_receipt_restart_uses_same_response(self):
        loop=self.loop();before=self.c.state()
        with patch.object(loop,'_proposal',side_effect=ValueError('Injected local failure')):
            with self.assertRaises(ModelUnavailable):loop.run_turn('Hello')
        pending=self.c.resume()['session']['pending_action']
        stored=json.loads(loop._previous_attempt(pending['request_id'])['proposal'])
        self.assertEqual(json.loads(stored['received']),self.n.response)
        self.assertEqual(self.c.state(),before)
        result=self.loop().run_turn(resume=True)
        self.assertEqual(result['request_id'],pending['request_id'])
        self.assertEqual(self.n.calls,1)
        self.assertEqual(result['status'],'recorded')
        self.loop().run_turn(resume=True)
        self.assertEqual(self.n.calls,1)

    def test_raw_invalid_receipt_and_explicit_retry_preserve_history(self):
        def invalid(prompt):
            self.n.calls+=1
            self.n.receipt_callback('not JSON')
            raise ModelUnavailable('Invalid JSON')
        with patch.object(self.n,'generate',side_effect=invalid):
            with self.assertRaises(ModelUnavailable):self.loop().run_turn('Hello')
        with self.assertRaises(ModelUnavailable):self.loop().run_turn(resume=True)
        self.assertEqual(self.n.calls,1)
        self.assertEqual(self.loop().run_turn(resume=True,retry=True)['status'],'recorded')
        self.assertEqual(self.n.calls,2)
        with self.c.transaction() as con:
            row=con.execute('SELECT proposal,reason FROM narrative_attempt_history').fetchone()
            self.assertIn('not JSON',row['proposal'])
            self.assertIn('JSON',row['reason'])

    def test_planner_apply_failure_reuses_receipt_after_restart(self):
        with self.c.transaction() as con:
            MemoryIndex(con).sync()
            WorldPlanner(con).initialize()
            Agenda(con).initialize()
            install_test_chronicle(con)
        prepared=self.c.prepare_turn('I work for several days',narrative_memory=True)
        planner=FakePlanner()
        with patch.object(WorldPlanner,'apply_review',side_effect=ValueError('Local error')):
            with self.assertRaises(ModelUnavailable):review_world(self.c,planner,prepared)
        result=review_world(Campaign(self.path),planner,prepared)
        self.assertEqual(result['model_calls'],0)
        self.assertEqual(planner.calls,1)
        self.assertEqual(review_world(Campaign(self.path),planner,prepared)['model_calls'],0)

    def test_received_version_does_not_replace_concurrent_state(self):
        loop=self.loop()
        with patch.object(loop,'_proposal',side_effect=OSError('Crash')):
            with self.assertRaises(OSError):loop.run_turn('Hello')
        with self.c.transaction() as con:
            state=self.c._state(con);state['state_version']+=1
            con.execute('UPDATE campaign SET state=?',(json.dumps(state),))
        before=self.c.state()
        result=self.loop().run_turn(resume=True)
        self.assertEqual(result['status'],'rejected')
        self.assertEqual(self.c.state(),before)
        self.assertEqual(self.n.calls,1)


class GroupTests(Fixture):
    def put_group(self,count,minute=10,reason='Fictional deadline.'):
        with self.c.transaction() as con:
            agenda=Agenda(con);agenda.initialize()
            for i in range(count):
                agenda._put({'entry_id':f'fictional:{i}','expected_revision':-1,'title':'Test',
                    'actor':'guard','location':'gate','kind':'verification','due_minute':minute,
                    'conditions':[],'references':['action:test'],'audience':['narrator','player'],
                    'interrupt':False,'status':'planned','reason':reason,'outcome_event_index':None},'fixture',0)

    def clear(self):
        with self.c.transaction() as con:
            for table in ('world_agenda','agenda_history'):con.execute('DELETE FROM '+table)

    def test_seventeen_and_limit_are_complete_and_resolvable(self):
        for count in (17,RESPONSE_LIMIT):
            with self.subTest(count=count):
                self.put_group(count)
                prepared=self.c.prepare_turn('Wait',narrative_memory=True)
                packet=prepared['world_agenda']
                self.assertEqual(len(packet['events']),count)
                self.assertIsNone(packet['technical_diagnostic'])
                events=[];updates=[]
                for i,entry in enumerate(packet['events']):
                    events.append({'world_minute':10,'description':'Fictional check finished.',
                        'causes':[entry['reference']],'conditions':[],'changes':[],'asset_operations':[],
                        'audience':['narrator','player']})
                    updates.append({'entry_id':entry['entry_id'],'status':'finished','reason':'Test finished.',
                        'references':[entry['reference']],'outcome_event_index':i,'due_minute':None,'creation':None})
                self.n.response={'narrative_schema_version':'1.2','narration':'The checks are finished.',
                    'events':events,'blocking_questions':[],'memory_request':None,'memory_updates':[],
                    'agenda_updates':updates,'continuation':None,'roll_checks':[]}
                result=self.loop().run_turn(resume=True)
                self.assertEqual(result['status'],'recorded',result)
                with self.c.transaction() as con:
                    self.assertEqual(con.execute("SELECT COUNT(*) FROM world_agenda WHERE status='finished'").fetchone()[0],count)
                self.clear()

    def test_dense_or_oversized_group_diagnosed_before_call(self):
        for count,reason in ((RESPONSE_LIMIT+1,'Short.'),(8,'x'*3000)):
            with self.subTest(count=count):
                self.put_group(count,reason=reason)
                before=self.c.state()
                self.c.begin_action('Wait')
                with self.assertRaisesRegex(ModelUnavailable,'dense'):self.loop().run_turn(resume=True)
                self.assertEqual(self.n.calls,0)
                self.assertEqual(self.c.state(),before)
                self.clear()

    def test_overdue_group_never_creates_past_horizon(self):
        self.put_group(2,minute=0)
        packet=self.c.prepare_turn('Wait')['world_agenda']
        self.assertEqual(len(packet['events']),2)
        self.assertIsNone(packet['max_horizon_if_limited'])

    def test_many_overdue_groups_are_mandatory_up_to_response_capacity(self):
        with self.c.transaction() as con:
            state=self.c._state(con);state['world_minute']=100
            con.execute('UPDATE campaign SET state=?',(json.dumps(state),))
        self.put_group(17,minute=1)
        with self.c.transaction() as con:
            agenda=Agenda(con)
            for i in range(17):
                entry=agenda.get(f'fictional:{i}')
                entry['due_minute']=i+1
                con.execute('UPDATE world_agenda SET due_minute=?,payload=? WHERE entry_id=?',
                    (i+1,json.dumps({k:v for k,v in entry.items() if k!='revision'}),entry['entry_id']))
        packet=self.c.prepare_turn('Wait')['world_agenda']
        self.assertEqual(len(packet['events']),17)
        self.assertIsNone(packet['technical_diagnostic'])


# Fictional rule set: no story-specific ability. Costs are percentages of the maximum HP.
MECHANICS_RULES={
    'version':'fictional-1','hp_max':10000,'hunger_from_health':True,'forbidden_resources':['player_hunger'],
    'solar_schedule':{'first_sunrise_minute':360,'first_sunset_minute':1080},
    'regeneration':{'day':{'percent':1,'seconds':3},'night':{'percent':1,'seconds':1},
                    'drunk_blood':{'percent':5,'seconds':1}},
    'abilities':{
        'bolt':{'phase':'always','catalogue':{'offensive':True,'aliases':['lightning']}},
        'ward':{'phase':'always'},
        'swarm':{'phase':'always'},
        'mind_link':{'phase':'always'},'charm':{'phase':'always'},'trance':{'phase':'always'},
        'rampart':{'phase':'always'},
        'domain':{'phase':'night'},
        'dawn_shield':{'phase':'always','effect':'death_prevention',
                       'parameters':{'duration_seconds':60,'lethal_prevention_capacity_hp_max_percent':50,
                                     'survival_hp_max_percent':1}},
    },
    'charges':{
        'player_resurrections_left':{'period':'night','consumed_by':'resurrection','phase':'night'},
        'player_domains_left':{'period':'night','ability':'domain'},
        'player_death_refusal_available':{'period':'combat','ability':'dawn_shield','consumed_by':'prevent_death'},
        'player_rampart_available':{'period':'combat','ability':'rampart'},
    },
}
COST_RULES={'default_output_percent':10,
            'full_power_cost_percent':{'bolt':15,'ward':10,'swarm':40,'mind_link':0,'charm':0,'trance':0,
                                       'rampart':0,'domain':0,'dawn_shield':20}}


class MechanicFixture(Fixture):
    def setUp(self):
        super().setUp()
        with self.c.transaction() as con:
            state=self.c._state(con);config=json.loads(con.execute('SELECT config FROM campaign').fetchone()[0])
            state['world_minute']=360
            state['resources'].update(player_hp=5000,player_hp_max=10000,night_id=1,
                player_resurrections_left=1,player_domains_left=1,
                player_combat_id=0,player_combat_active=0,player_death_refusal_available=1,
                player_rampart_available=1)
            config['character_rules']=deepcopy(MECHANICS_RULES)
            config['spell_cost_rules']=deepcopy(COST_RULES)
            config['mechanics']={'version':'vitality-1.0'}
            con.execute('UPDATE campaign SET state=?,config=?',(json.dumps(state),json.dumps(config)))

    def operation(self,kind='ability',at=None,ability='bolt',output=10,amount=None,start=None):
        return {'kind':kind,'at_second':instant(self.c.state()) if at is None else at,
            'ability_id':ability if kind=='ability' else None,'output_percent':output if kind=='ability' else None,
            'amount':amount,'start_second':start,'source_reference':'demo:initial_configuration'}

    def apply_ops(self,ops,end=None,changes=None,claims=None):
        state=self.c.state();end=instant(state) if end is None else end
        e=event(state,'Fictional consequence.',changes or [],minute=end//60)
        e.update(world_second=end%60,mechanical_operations=ops,public_consequences=claims or [])
        p=proposal(state,'Fictional result.',[e])
        return self.c.apply(p),p

    def set_state(self,**fields):
        with self.c.transaction() as con:
            state=self.c._state(con)
            resources=fields.pop('resources',{})
            state.update(fields);state['resources'].update(resources)
            con.execute('UPDATE campaign SET state=?',(json.dumps(state),))


class MechanicsTests(MechanicFixture):

    def test_costs_free_spells_and_idempotence(self):
        for output,cost in ((10,150),(25,375),(100,1500)):
            before=self.c.state()['resources']['player_hp']
            result,p=self.apply_ops([self.operation(output=output)])
            self.assertEqual(result['status'],'recorded',result)
            self.assertEqual(self.c.state()['resources']['player_hp'],before-cost)
            self.assertEqual(Campaign(self.path).apply(p),result)
            self.assertEqual(self.c.state()['resources']['player_hp'],before-cost)
        for key in ('mind_link','charm','trance'):
            before=self.c.state()['resources']['player_hp']
            self.assertEqual(self.apply_ops([self.operation(ability=key)])[0]['status'],'recorded')
            self.assertEqual(self.c.state()['resources']['player_hp'],before)

    def test_invalid_power_unknown_insufficient_and_manual_change_rollback(self):
        for bad in (self.operation(ability='unknown'),self.operation(output=0),
                    self.operation(output=-1),self.operation(output=101),self.operation(ability='swarm',output=100)):
            before=self.c.state()
            # Insufficient health fixture for the costly operation only.
            if bad['ability_id']=='swarm':
                self.set_state(resources={'player_hp':200})
                before=self.c.state()
            try:result,_=self.apply_ops([bad])
            except ValueError:result={'status':'rejected'}
            self.assertEqual(result['status'],'rejected')
            self.assertEqual(self.c.state(),before)
        before=self.c.state()
        result,_=self.apply_ops([self.operation()],changes=[{'operation':'increment','path':'/resources/player_hp','value':-150}])
        self.assertEqual(result['status'],'rejected')
        self.assertEqual(self.c.state(),before)

    def test_fraction_preserved_across_three_seconds_and_reads(self):
        start=instant(self.c.state());before=self.c.state()
        for i in range(1,4):
            self.assertEqual(self.apply_ops([],start+i)[0]['status'],'recorded')
        split=self.c.state()['resources']
        self.assertEqual(split['player_hp'],5100)
        self.assertEqual(split['player_regen_rest_num'],0)
        with self.c.transaction() as con:con.execute('UPDATE campaign SET state=?',(json.dumps(before),))
        self.apply_ops([],start+3)
        self.assertEqual(self.c.state()['resources'],split)
        saved=self.c.state();self.c.resume();self.loop().run_turn(resume=True)
        self.assertEqual(self.c.state(),saved)

    def test_solar_boundary_and_no_blood_double_count(self):
        self.set_state(world_minute=1079,world_second=59,resources={'player_resurrections_left':0,'player_domains_left':0})
        self.apply_ops([],1080*60+1)
        self.assertEqual(self.c.state()['resources']['player_hp'],5133)
        self.assertEqual(self.c.state()['resources']['night_id'],2)
        self.assertEqual(self.c.state()['resources']['player_resurrections_left'],1)
        start=1080*60+1
        result,p=self.apply_ops([self.operation('blood_interval',at=start+1,start=start)],start+1)
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(self.c.state()['resources']['player_hp'],5633)
        self.c.apply(p)
        self.assertEqual(self.c.state()['resources']['player_hp'],5633)
        before=self.c.state()
        self.assertEqual(self.apply_ops([self.operation('blood_interval',at=start+1,start=start)],start+1)[0]['status'],'rejected')
        self.assertEqual(self.c.state(),before)

    def test_combat_cycles_domain_charge_and_resurrection(self):
        self.apply_ops([self.operation('combat_start')])
        self.assertEqual(self.c.state()['resources']['player_combat_id'],1)
        self.assertEqual(self.apply_ops([self.operation('combat_start')])[0]['status'],'rejected')
        self.apply_ops([self.operation(ability='rampart')])
        self.assertEqual(self.c.state()['resources']['player_rampart_available'],0)
        self.apply_ops([self.operation('combat_end'),self.operation('combat_start')])
        self.assertEqual(self.c.state()['resources']['player_combat_id'],2)
        self.assertEqual(self.c.state()['resources']['player_rampart_available'],1)
        self.set_state(world_minute=1080,world_second=0)
        self.apply_ops([self.operation(ability='domain')])
        self.assertEqual(self.c.state()['resources']['player_domains_left'],0)
        self.assertEqual(self.apply_ops([self.operation(ability='domain')])[0]['status'],'rejected')
        result,p=self.apply_ops([self.operation('damage',amount=10000),self.operation('resurrection')])
        self.assertEqual(result['status'],'recorded',result)
        self.assertEqual(self.c.state()['resources']['player_hp'],5000)
        self.assertEqual(self.c.state()['resources']['player_resurrections_left'],0)
        self.assertEqual(self.c.state()['resources']['player_death_refusal_available'],1)
        self.c.apply(p)
        self.assertEqual(self.c.state()['resources']['player_hp'],5000)

    def test_death_stays_dead_and_health_capped_and_ambiguous_prevention_refused(self):
        self.set_state(resources={'player_hp':9950})
        start=instant(self.c.state());self.apply_ops([],start+3)
        self.assertEqual(self.c.state()['resources']['player_hp'],10000)
        self.apply_ops([self.operation('damage',amount=10000)])
        self.apply_ops([],start+6)
        self.assertEqual(self.c.state()['resources']['player_hp'],0)
        self.assertEqual(self.apply_ops([self.operation('prevent_death')])[0]['status'],'rejected')

    def test_response_13_roundtrip_and_schema_compilation(self):
        from novel_engine.schema_codex import compile_schema
        from novel_engine.narration_codex import response_schema
        wire,_=compile_schema(response_schema(version='1.3'))
        self.assertEqual(wire['type'],'object')
        operation=self.operation();operation['source_reference']='current_action'
        self.n.response={'narrative_schema_version':'1.3','narration':'The power is cast.',
            'events':[{'world_minute':360,'world_second':0,'description':'Cast.',
                'causes':['current_action'],'conditions':[],'changes':[],'audience':['narrator','player'],
                'asset_operations':[],'mechanical_operations':[operation],'public_consequences':[]}],
            'blocking_questions':[],'memory_request':None,'memory_updates':[],
            'agenda_updates':[],'continuation':None,'roll_checks':[]}
        result=self.loop().run_turn('I cast a bolt.')
        self.assertEqual(result['status'],'recorded',result)
        self.assertEqual(self.c.state()['resources']['player_hp'],4850)

    def test_older_response_version_refused_on_a_mechanics_campaign(self):
        self.n.response={'narration':'The power is cast.','events':[],'blocking_questions':[]}
        with self.assertRaises(ModelUnavailable):self.loop().run_turn('I cast a bolt.')
        self.assertEqual(self.c.state()['resources']['player_hp'],5000)

    def test_dawn_shield_prevents_lethal_hit_at_one_percent_with_distinct_charge(self):
        self.set_state(world_minute=1080,world_second=0)
        result,_=self.apply_ops([self.operation('combat_start'),
            self.operation(ability='dawn_shield',output=100),
            self.operation('prevent_death',amount=3000)])
        self.assertEqual(result['status'],'recorded',result)
        resources=self.c.state()['resources']
        self.assertEqual(resources['player_hp'],100)
        self.assertEqual(resources['player_death_refusal_available'],0)
        self.assertEqual(resources['player_resurrections_left'],1)
        self.assertEqual(self.apply_ops([self.operation('prevent_death',amount=500)])[0]['status'],'rejected')
        self.assertEqual(self.apply_ops([self.operation('resurrection')])[0]['status'],'rejected')

    def test_zero_cost_spell_and_legacy_bypass_and_zero_payment_boundary(self):
        self.set_state(world_minute=1080,world_second=0)
        before=self.c.state()['resources']['player_hp']
        self.assertEqual(self.apply_ops([self.operation(ability='mind_link')])[0]['status'],'recorded')
        self.assertEqual(self.c.state()['resources']['player_hp'],before)
        result,_=self.apply_ops([],changes=[{'operation':'set','path':'/resources/player_hp','value':1}])
        self.assertEqual(result['status'],'rejected')
        self.set_state(world_minute=360,world_second=0,resources={'player_hp':150})
        result,_=self.apply_ops([self.operation()])
        self.assertEqual(result['status'],'rejected')
        self.assertIn('exactly zero',result['reason'])
        self.assertEqual(self.c.state()['resources']['player_hp'],150)

    def test_late_condition_rolls_back_cost_and_nothing_rejected_is_indexed(self):
        with self.c.transaction() as con:MemoryIndex(con).sync()
        state=self.c.state();first=event(state,'Fictional cast.',[])
        first['mechanical_operations']=[self.operation()]
        second=event(state,'Unique rejected fact zirconium.',[],conditions=[{'path':'/positions/player','equals':'absent'}])
        result=self.c.apply(proposal(state,'Result that will not be displayed.',[first,second]))
        self.assertEqual(result['status'],'rejected')
        self.assertEqual(result['narration'],'')
        self.assertEqual(self.c.state(),state)
        with self.c.transaction() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM events').fetchone()[0],0)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM memory_sources WHERE text LIKE '%zirconium%'").fetchone()[0],0)


class PublicTests(MechanicFixture):
    def claim(self,kind,path,value,source='demo:initial_configuration'):
        return {'kind':kind,'path':path,'value_json':json.dumps(value),'source_reference':source}

    def test_arrival_matches_final_position_and_existing_arrival_needs_no_mutation(self):
        claim=self.claim('arrival','/positions/player','route')
        before=self.c.state()
        self.assertEqual(self.apply_ops([],claims=[claim])[0]['status'],'rejected')
        self.assertEqual(self.c.state(),before)
        self.assertEqual(self.apply_ops([],changes=[{'operation':'set','path':'/positions/player','value':'route'}],claims=[claim])[0]['status'],'recorded')
        self.assertEqual(self.apply_ops([],claims=[claim])[0]['status'],'recorded')

    def test_health_public_value_is_locally_computed_not_freely_declared(self):
        before=self.c.state()
        result,_=self.apply_ops([self.operation()],claims=[self.claim('damage','/resources/player_hp',4900)])
        self.assertEqual(result['status'],'rejected')
        self.assertEqual(self.c.state(),before)
        result,p=self.apply_ops([self.operation()],claims=[self.claim('damage','/resources/player_hp',4850)])
        self.assertEqual(result['status'],'recorded')
        from novel_engine.consequences import public_projection
        self.assertEqual(public_projection(result['applied_events'])[0]['value'],4850)
        self.assertNotIn('source_reference',public_projection(result['applied_events'])[0])
        self.c.apply(p)
        self.assertEqual(self.c.state()['resources']['player_hp'],4850)

    def test_healing_without_elapsed_time_is_rejected(self):
        self.assertEqual(self.apply_ops([],claims=[self.claim('healing','/resources/player_hp',5100)])[0]['status'],'rejected')
        end=instant(self.c.state())+3
        self.assertEqual(self.apply_ops([],end,claims=[self.claim('healing','/resources/player_hp',5100)])[0]['status'],'recorded')

    def test_secret_knowledge_rejected_but_public_manifestation_keeps_private_cause(self):
        state=self.c.state();private=event(state,'Fictional secret.',[],audience=['narrator'])
        self.assertEqual(self.c.apply(proposal(state,'Silence.',[private]))['status'],'recorded')
        state=self.c.state()
        e=event(state,'A noise is perceptible.',[{'operation':'set','path':'/knowledge/player','value':['noise']}],causes=[private['event_id']])
        e['public_consequences']=[self.claim('knowledge','/knowledge/player',['noise'],private['event_id'])]
        before=self.c.state()
        self.assertEqual(self.c.apply(proposal(state,'You hear a noise.',[e]))['status'],'rejected')
        self.assertEqual(self.c.state(),before)
        e['public_consequences'][0]['source_reference']=e['event_id']
        result=self.c.apply(proposal(state,'You hear a noise.',[e]))
        self.assertEqual(result['status'],'recorded',result)
        from novel_engine.consequences import public_projection
        self.assertNotIn(private['event_id'],json.dumps(public_projection(result['applied_events'])))

    def test_purchase_claim_requires_operation_and_correct_balance(self):
        with self.c.transaction() as con:
            state=self.c._state(con);state['resources']['player_silver']=22
            config=json.loads(con.execute('SELECT config FROM campaign').fetchone()[0])
            config['possession_rules']={'version':1,'currencies':{'player_silver':{'owner_id':'player','label':'Silver coins'}}}
            con.execute('UPDATE campaign SET state=?,config=?',(json.dumps(state),json.dumps(config)))
        state=self.c.state();e=event(state,'Fictional purchase.',[])
        e['public_consequences']=[self.claim('payment','/resources/player_silver',19)]
        self.assertEqual(self.c.apply(proposal(state,'You pay three coins.',[e]))['status'],'rejected')
        e['asset_operations']=[op('purchase',-3,outputs={'wood':item(1)})]
        result=self.c.apply(proposal(state,'You pay three coins.',[e]))
        self.assertEqual(result['status'],'recorded',result)
        self.assertEqual(self.c.state()['resources']['player_silver'],19)

    def test_recall_or_intention_cannot_create_acquired_result(self):
        path='/positions/player';value=self.c.state()['positions']['player']
        self.assertEqual(self.apply_ops([],claims=[self.claim('recall',path,value)])[0]['status'],'recorded')
        before=self.c.state()
        self.assertEqual(self.apply_ops([],changes=[{'operation':'set','path':path,'value':'route'}],claims=[self.claim('intention',path,'route')])[0]['status'],'rejected')
        self.assertEqual(self.c.state(),before)

    def test_boolean_and_private_actor_are_not_public_resource_values(self):
        before=self.c.state()
        self.assertEqual(self.apply_ops([],claims=[self.claim('recall','/resources/player_resurrections_left',True)])[0]['status'],'rejected')
        self.assertEqual(self.c.state(),before)
        self.assertEqual(self.apply_ops([],claims=[self.claim('recall','/intentions/faction',before['intentions']['faction'])])[0]['status'],'rejected')


def inventory_fixture(campaign,count):
    """Large inventory of exact items with unknown quantities, saved without any projection."""
    with campaign.transaction() as con:
        state=campaign._state(con)
        state['inventory']={f'object_{i:04d}':{**item(None,'unknown'),'references':[f'fixture:object:{i}'],
                                               'description':f'Fictional object number {i}.'}
                            for i in range(count)}
        con.execute('UPDATE campaign SET state=?',(json.dumps(state),))
        MemoryIndex(con).sync()


def history_fixture(campaign,pairs):
    """Long journal where one old promise and its answer are the only mentions of the sapphire key."""
    with campaign.transaction() as con:
        for number in range(1,pairs+1):
            if number==10:
                said,answer='I promise to guard the sapphire key until dawn.','The sapphire key is handed over to the harbor master.'
            else:
                said,answer=f'Ordinary question number {number}.',f'Ordinary routine answer number {number}.'
            rid=f'fixture-{number}'
            con.execute('INSERT INTO exchanges(message_id,role,content) VALUES (?,?,?)',(rid,'player',said))
            con.execute('INSERT INTO exchanges(message_id,role,content) VALUES (?,?,?)',(rid+':narrator','narrator',answer))
            con.execute('INSERT INTO requests VALUES (?,?,?)',(rid,'fixture',json.dumps({'status':'recorded','world_minute_after':0})))
        MemoryIndex(con).sync()


class ContextTests(Fixture):
    def test_thousand_items_selection_preserves_target_and_recall_path(self):
        inventory_fixture(self.c,1000)
        prepared=self.c.prepare_turn('I take object_0999.',narrative_memory=True)
        loop=self.loop();prompt=loop.build_prompt(prepared)
        packet=json.loads(prompt.rsplit('\nCURRENT TURN\n',1)[1])
        self.assertIn('object_0999',packet['current_state']['inventory'])
        self.assertEqual(packet['current_state']['inventory']['object_0999']['quantity'],None)
        self.assertNotIn('fixture:object:500',packet['allowed_references'])
        self.assertIn('object_0500',packet['omitted_entities_index']['inventory'])
        self.assertEqual(len(self.c.state()['inventory']),1000)
        self.assertLess(len(prompt.encode()),loop.context_budget_bytes)
        before=self.c.state()
        self.assertEqual(self.c.recover_memory(prepared['model_proposal']['request_id'],'object:object_0500')['search_status'],'ready')
        prompt=loop.build_prompt(self.c.prepare_turn(narrative_memory=True))
        packet=json.loads(prompt.rsplit('\nCURRENT TURN\n',1)[1])
        self.assertIn('object_0500',packet['current_state']['inventory'])
        self.assertEqual(self.c.state(),before)

    def test_old_fact_recalled_from_thousand_exchanges_with_intention_distinct(self):
        history_fixture(self.c,500)
        prepared=self.c.prepare_turn('What do I know about the sapphire key?',narrative_memory=True)
        packet=json.loads(self.loop().build_prompt(prepared).rsplit('\nCURRENT TURN\n',1)[1])
        exchanges=packet['recalled_exchanges']
        self.assertTrue(any(e['role']=='player' and 'promise' in e['content'] for e in exchanges))
        self.assertTrue(any(e['role']=='narrator' and 'is handed over' in e['content'] for e in exchanges))
        self.assertFalse(any(e['role']=='narrator' and 'sapphire' in e['content'] for e in packet['recent_exchanges']))
        for e in exchanges:
            if e['role']=='narrator':self.assertIn('exchange:'+str(e['seq']),packet['allowed_references'])

    def test_document_cache_refreshes_in_injected_root(self):
        root=Path(self.temp.name)/'fictional_docs'
        for relative in ('docs/engine/common_rules.md','docs/engine/agents/narrator.md'):
            path=root/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('Version one.',encoding='utf-8')
        loop=NarrativeLoop(self.c,self.n,documents_root=root)
        prepared=self.c.prepare_turn('Hello')
        first=loop.build_prompt(prepared);signature=loop.last_metrics['document_signature']
        (root/'docs/engine/common_rules.md').write_text('Version two, updated.',encoding='utf-8')
        second=loop.build_prompt(prepared)
        self.assertNotEqual(first,second)
        self.assertNotEqual(signature,loop.last_metrics['document_signature'])
        self.assertNotIn('Version one.',json.loads(second.split('PREPARATION DOCUMENTS\n')[1].split('\nCURRENT TURN\n')[0])[0]['text'])

    def test_budget_refuses_before_generation_and_references_match_actual_packet(self):
        loop=NarrativeLoop(self.c,self.n,context_budget_bytes=100)
        before=self.c.state()
        with self.assertRaisesRegex(ModelUnavailable,'budget'):loop.run_turn('Hello')
        self.assertEqual(self.n.calls,0)
        self.assertEqual(loop.last_metrics['model_calls'],0)
        self.assertEqual(self.c.state(),before)
        # Local budget fix: preparing needs no new authorized call.
        loop.context_budget_bytes=196608
        prepared=self.c.prepare_turn(narrative_memory=True)
        packet=json.loads(loop.build_prompt(prepared).rsplit('\nCURRENT TURN\n',1)[1])
        self.assertEqual(set(packet['allowed_references']),packet_references(packet))
        self.assertEqual(loop.run_turn(resume=True)['status'],'recorded')

    def test_optional_context_removed_before_interruptive_agenda(self):
        prepared=self.c.prepare_turn('Hello',narrative_memory=True)
        prepared['world_planning']={'situations':[{'reference':'plan:over_budget','description':'x'*(CONTEXT_BUDGET_BYTES+4096)}]}
        prepared['world_agenda']['events']=[{'entry_id':'fixture','reference':'agenda:fixture',
            'due_minute':1,'conditions':[{'path':'/positions/player','equals':'town_gate'}],
            'references':['demo:initial_configuration'],'interrupt':True,'audience':['narrator']}]
        packet=json.loads(self.loop().build_prompt(prepared).rsplit('\nCURRENT TURN\n',1)[1])
        self.assertNotIn('world_planning',packet)
        self.assertNotIn('plan:over_budget',packet['allowed_references'])
        self.assertEqual(packet['world_agenda']['events'],prepared['world_agenda']['events'])

    def test_final_reference_size_triggers_optional_reduction(self):
        prepared=self.c.prepare_turn('Hello',narrative_memory=True)
        prepared['world_planning']={'situations':[{
            'reference':'plan:optional','description':'x'*3000,
            'provenance':{'references':['plan:detailed_source:'+str(i) for i in range(100)]}}]}
        original=deepcopy(prepared)
        loop=self.loop()
        complete=loop.build_prompt(prepared)
        before=json.loads(complete.rsplit('\nCURRENT TURN\n',1)[1])
        loop.context_budget_bytes=len(complete.encode('utf-8'))-128
        reduced=loop.build_prompt(prepared)
        after=json.loads(reduced.rsplit('\nCURRENT TURN\n',1)[1])
        self.assertLessEqual(len(reduced.encode('utf-8')),loop.context_budget_bytes)
        self.assertNotIn('world_planning',after)
        self.assertIn('world_planning',after['omitted_blocks'])
        self.assertEqual(set(after['allowed_references']),packet_references(after))
        self.assertFalse(any(ref.startswith('plan:detailed_source:') for ref in after['allowed_references']))
        for key in ('current_state','campaign_rules','world_agenda','recent_exchanges','recalled_exchanges'):
            self.assertEqual(after.get(key),before.get(key),key)
        self.assertEqual(prepared,original)
        self.assertEqual(self.n.calls,0)

    def test_exact_prompt_retained_after_crash(self):
        loop=self.loop();captured=[];generate=self.n.generate
        def receive(prompt):captured.append(prompt);return generate(prompt)
        with patch.object(self.n,'generate',side_effect=receive),patch.object(loop,'_proposal',side_effect=OSError('Crash')):
            with self.assertRaises(OSError):loop.run_turn('Hello')
        pending=self.c.resume()['session']['pending_action']
        saved=json.loads(loop._previous_attempt(pending['request_id'])['proposal'])
        self.assertEqual(saved['prompt'],captured[0])
        self.assertEqual(self.loop().run_turn(resume=True)['status'],'recorded')
        self.assertEqual(self.n.calls,1)


class InterruptionTests(Fixture):
    def test_interruption_cannot_be_crossed_inside_the_same_minute(self):
        with self.c.transaction() as con:
            agenda=Agenda(con);agenda.initialize()
            agenda._put({'entry_id':'fixture:instant','expected_revision':-1,'title':'Fictional choice.',
                'actor':'guard','location':'post','kind':'verification','due_minute':361,'conditions':[],
                'references':['demo:initial_configuration'],'audience':['narrator','player'],'interrupt':True,
                'status':'planned','reason':'Precise fixture.','outcome_event_index':None},'fixture',360)
        state=self.c.state()
        arrival=event(state,'A choice is needed.',[],minute=361,causes=['agenda:fixture:instant'])
        arrival.update(world_second=0,mechanical_operations=[])
        later=event(state,'Step after the choice.',[],minute=361);later['world_second']=1
        with self.c.transaction() as con:entry=Agenda(con).get('fixture:instant')
        entry.pop('revision');entry.update(expected_revision=0,status='finished',outcome_event_index=0)
        changes={'updates':[entry],'continuation':{'goal':'Wait','remaining':'Choice expected.','status':'suspended','event_index':0},'roll_checks':[]}
        result=self.c.apply(proposal(state,'A choice appears.',[arrival,later]),agenda_changes=changes)
        self.assertEqual(result['status'],'rejected')
        self.assertIn('interruption',result['reason'])
        self.assertEqual(self.c.state(),state)
        result=self.c.apply(proposal(state,'A choice appears.',[arrival]),agenda_changes=changes)
        self.assertEqual(result['status'],'recorded',result)
        self.assertEqual(self.c.state().get('world_second',0),0)


if __name__=='__main__':unittest.main()
