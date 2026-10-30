"""Summarizer, chronicler, mandatory deadlines and resumes without a remote call."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from novel_engine import planner
from novel_engine.adapters import ModelUnavailable
from novel_engine.agenda import Agenda
from novel_engine.campaign import Campaign
from novel_engine.demo import initial_state,configuration,event,proposal
from novel_engine.indexed_memory import MemoryIndex
from novel_engine.narrative import NarrativeLoop
from novel_engine.planner import WorldPlanner,CodexPlanner,planner_schema,long_wait,long_activity,review_world,MARKER
from novel_engine.scripts.install_chronicler import install
from novel_engine.scripts.migrate_planner import migrate,preserved_fingerprint
from novel_engine.scripts.verify_game_kit import check_schema


class FakePlanner:
    def __init__(self):
        self.calls=0;self.last_metrics={};self.packets=[];self.transform=None

    def generate(self,prompt):
        self.calls+=1
        packet=json.loads(prompt.rsplit(MARKER,1)[1]);self.packets.append(packet)
        exchanges=packet['exchanges_to_summarize'];blocks=packet['summaries_to_consolidate']
        response={'planner_schema_version':'2.0',
                  'player_summary':{'text':'The recorded actions are accomplished; the open projects remain to be resolved.',
                      'references':[e['reference'] for e in exchanges]} if exchanges else None,
                  'old_synthesis':{'text':'Synthesis of the periods played, with commitments still open.',
                      'references':[s['reference'] for s in blocks]} if blocks else None,
                  'chronicle':{'clocks':[],'manifestations':[]} if 'chronicle' in packet else None}
        return self.transform(packet,response) if self.transform else response


class FakeNarrator:
    def __init__(self):self.calls=0;self.last_metrics={};self.transform=None
    def generate(self,prompt):
        self.calls+=1
        packet=json.loads(prompt.rsplit('\nCURRENT TURN\n',1)[1])
        response={'narration':'The guard answers your question.','events':[],'blocking_questions':[]}
        return self.transform(packet,response) if self.transform else response


CLOCK={'id':'bandits','faction':'Hill bandits','goal':'Control the merchant road.',
       'segments':4,'progression':1,'status':'in_progress','signs':'Convoys delayed, merchants armed.',
       'event':'The bandits block the road and ransom the convoys.','origin':'canon',
       'reason':'Adapted arc.','references':['chronology:raid']}


def install_test_chronicle(con,progression=2,segments=3):
    """Invented clock that triggers a chronicle on the first turn, without a canonical source."""
    WorldPlanner(con).install_chronicle([{**CLOCK,'id':'messenger','faction':'Messagers','origin':'invention',
        'segments':segments,'progression':progression,'references':['demo:initial_configuration']}],
        'tests/fixtures/chronology.json')


def advance(packet,step=1,**fields):
    clock=packet['chronicle']['clocks'][0]
    item={k:v for k,v in clock.items() if k not in ('revision','reference')}
    item.update(expected_revision=clock['revision'],progression=clock['progression']+step,
                reason='Time passes.',references=[*clock['references'],clock['reference']],**fields)
    return item


def sign(packet,identifier='rumor',kind='sign',delay=5):
    return {'id':identifier,'clock':packet['chronicle']['clocks'][0]['id'],'type':kind,
            'title':'Rumor from the road','actor':'travelers','place':'gate',
            'description':'Travelers talk about attacked convoys.',
            'perception':'The guard talks about it at the gate.',
            'due_minute':packet['current_state']['world_minute']+delay}


class PlannerTests(unittest.TestCase):
    def setUp(self):
        base=ROOT/'.tmp-tests';base.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=base)
        self.c=Campaign(Path(self.temp.name)/'novel_engine.sqlite').create(initial_state(),configuration())
        self.c.open_scene('The guard waits at the door.')
        chronology=Path(self.temp.name)/'chronology.json'
        chronology.write_text(json.dumps({'nodes':[{'id':'raid','title':'Raid on the road','phase':'volume_2'}]}),encoding='utf-8')
        self.chronology=chronology.relative_to(ROOT).as_posix()
        with self.c.transaction() as con:
            Agenda(con).initialize();MemoryIndex(con).sync()
            WorldPlanner(con).initialize()
        self.planner=FakePlanner();self.narrator=FakeNarrator()
        self.loop=NarrativeLoop(self.c,self.narrator,planner=self.planner)

    def tearDown(self):self.temp.cleanup()

    def period(self,value):
        patcher=patch.object(planner,'SUMMARY_PERIOD',value);patcher.start();self.addCleanup(patcher.stop)

    def chronicle(self,clock=None):
        with self.c.transaction() as con:WorldPlanner(con).install_chronicle([clock or CLOCK],self.chronology)

    def test_schema_and_standard_without_tools(self):
        check_schema(planner_schema())
        adapter=CodexPlanner(executable='codex.exe',effort='medium',timeout=180)
        command=adapter.command('schema','file')
        self.assertIn('service_tier="standard"',command)
        self.assertIn('model_reasoning_effort="medium"',command)
        self.assertIn('multi_agent',command)

    def test_long_wait_detection_not_conversation(self):
        for text in ('Routine for a month','I work for several days','I sleep until tomorrow',
                     'The weeks keep passing','I wait 24 hours','I work until the evening'):
            self.assertTrue(long_wait(text),text)
        for text in ('Hello','What are the market days?','I work for ten minutes','I wait for an answer'):
            self.assertFalse(long_wait(text),text)

    def test_long_activity_detection(self):
        for text in ('I train until something interrupts me','I read as long as nobody comes',
                     'I keep watch for as long as necessary','I search for several hours',
                     'I keep going until the interruption'):
            self.assertTrue(long_activity(text),text)
        for text in ('Hello','I work for ten minutes','I wait for an answer','I sleep until tomorrow'):
            self.assertFalse(long_activity(text),text)

    def test_startup_and_consultations_call_neither_role(self):
        before=self.c.state()
        self.loop.run_turn();self.c.world_agenda();self.c.memories('guard')
        self.assertEqual(self.planner.calls,0);self.assertEqual(self.narrator.calls,0)
        self.assertEqual(self.c.state(),before)

    def test_summary_only_after_period_and_light_packet(self):
        self.period(3)
        for i in range(2):self.loop.run_turn('A small clarification.')
        self.assertEqual(self.planner.calls,0)
        result=self.loop.run_turn('I ask for an answer.')
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(self.planner.calls,1);self.assertEqual(self.loop.last_metrics['model_calls'],2)
        packet=self.planner.packets[0]
        self.assertEqual([e['seq'] for e in packet['exchanges_to_summarize']],[1,3,5])
        self.assertEqual(packet['triggers'],['periodic_summary'])
        for key in ('chronicle','situations','targeted_proofs','world_agenda','documentary_canon'):
            self.assertNotIn(key,packet)
        self.assertEqual(set(packet['current_state']),{'state_version','versions','world_minute','date','positions'})
        self.assertEqual(planner_schema(packet)['properties']['chronicle'],{'type':'null'})
        for i in range(2):self.loop.run_turn('One more clarification.')
        self.assertEqual(self.planner.calls,1)

    def test_narrator_packet_has_no_old_tracks_or_private_notes(self):
        with self.c.transaction() as con:
            p=WorldPlanner(con)
            p._summary(1,1,{'text':'Promesse open.','references':['exchange:1']},['narrator','player'])
            p._summary(1,1,{'text':'Secret note.','references':['exchange:1']},['narrator'])
            con.execute('INSERT INTO planner_situations VALUES (?,?,?,?)',
                        ('old',0,'reserve',json.dumps({'id':'old','status':'reserve'})))
            packet=p.narrator_packet('Hello','gate')
        self.assertEqual(set(packet),{'player_summaries','instruction'})
        self.assertEqual(len(packet['player_summaries']),1)
        self.chronicle()
        with self.c.transaction() as con:packet=WorldPlanner(con).narrator_packet('Hello','gate')
        self.assertEqual(packet['world_in_motion'][0]['faction'],'Hill bandits')
        self.assertNotIn('event',packet['world_in_motion'][0])

    def test_failure_requires_explicit_retry_then_success(self):
        self.period(0)
        with patch.object(self.planner,'generate',side_effect=ModelUnavailable('Disconnection')):
            with self.assertRaises(ModelUnavailable):self.loop.run_turn('Hello.')
        with self.assertRaises(ModelUnavailable):self.loop.run_turn(resume=True)
        self.assertEqual(self.narrator.calls,0)
        result=self.loop.run_turn(resume=True,retry=True)
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(self.planner.calls,1)

    def test_crash_after_planner_response_reuses_without_second_call(self):
        self.period(0)
        with patch.object(WorldPlanner,'apply_review',side_effect=OSError('Stop before application')):
            with self.assertRaises(OSError):self.loop.run_turn('Hello.')
        self.assertEqual(self.planner.calls,1)
        result=self.loop.run_turn(resume=True)
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(self.planner.calls,1);self.assertEqual(self.narrator.calls,1)
        self.assertEqual(self.loop.last_metrics['model_calls'],1)

    def test_narrator_failure_does_not_repeat_applied_planner(self):
        self.period(0)
        with patch.object(self.narrator,'generate',side_effect=ModelUnavailable('Disconnection')):
            with self.assertRaises(ModelUnavailable):self.loop.run_turn('Hello.')
        self.loop.run_turn(resume=True,retry=True)
        self.assertEqual(self.planner.calls,1)

    def test_concurrent_state_change_refuses_review(self):
        self.period(1);self.loop.run_turn('Hello.')
        with self.c.transaction() as con:count=con.execute('SELECT COUNT(*) FROM planner_summaries').fetchone()[0]
        def change(packet,response):
            with self.c.transaction() as con:
                state=self.c._state(con);state['state_version']+=1
                con.execute('UPDATE campaign SET state=?',(json.dumps(state),))
            return response
        self.planner.transform=change
        with self.assertRaisesRegex(ModelUnavailable,'obsolete'):self.loop.run_turn('Hello.')
        self.assertEqual(self.narrator.calls,1)
        with self.c.transaction() as con:self.assertEqual(con.execute('SELECT COUNT(*) FROM planner_summaries').fetchone()[0],count)

    def test_summary_outside_period_refused(self):
        self.period(1);self.loop.run_turn('Hello.')
        def leak(packet,response):
            response['player_summary']['references']=['exchange:999'];return response
        self.planner.transform=leak
        before=self.c.state()
        with self.assertRaises(ModelUnavailable):self.loop.run_turn('Hello.')
        self.assertEqual(self.c.state(),before)

    def test_consolidation_retains_originals_and_sources(self):
        self.period(0)
        with self.c.transaction() as con:
            p=WorldPlanner(con)
            for i in range(6):p._summary(i+1,i+1,{'text':f'Period {i}, promise not fulfilled.',
                                               'references':['exchange:1']},['narrator','player'])
        self.loop.run_turn('Hello.')
        with self.c.transaction() as con:
            rows=con.execute('SELECT * FROM planner_summaries ORDER BY id').fetchall()
            self.assertEqual(len(rows),7)
            self.assertTrue(all(r['status']=='archive' for r in rows[:6]))
            synthesis=next(r for r in rows if r['level']==1)
            self.assertEqual(len(json.loads(synthesis['payload'])['references']),6)

    def test_migration_additive_and_repeatable(self):
        with self.c.transaction() as con:before=preserved_fingerprint(con)
        first=migrate(self.c);second=migrate(self.c)
        self.assertEqual(first['fingerprint_after'],before)
        self.assertEqual(second['fingerprint_after'],before)
        self.assertEqual(first['extractive_periods'],second['extractive_periods'])
        self.assertTrue(Path(first['backup']).is_file())
        with self.c.transaction() as con:self.assertEqual(con.execute('SELECT COUNT(*) FROM planner_situations').fetchone()[0],0)

    def test_public_summary_consultation_filters_private_notes(self):
        with self.c.transaction() as con:
            p=WorldPlanner(con)
            p._summary(1,1,{'text':'Promesse still open.','references':['exchange:1']},['narrator','player'])
            p._summary(1,1,{'text':'Secret project.','references':['demo:initial_configuration']},['narrator'])
        before=self.c.state()
        public=self.c.world_summaries()
        self.assertEqual(len(public['summaries']),1)
        self.assertNotIn('Secret project',json.dumps(public))
        self.assertEqual(self.c.state(),before)

    # Chronicler

    def test_installation_is_additive_backed_up_and_validated(self):
        result=install(self.c,[CLOCK],self.chronology)
        self.assertTrue(Path(result['backup']).is_file())
        self.assertEqual((result['added_clocks'],result['active_clocks']),(1,1))
        self.assertEqual(install(self.c,[CLOCK],self.chronology)['added_clocks'],0)
        for bad,message in (({**CLOCK,'id':'other','references':['chronology:absent']},'reference'),
                            ({**CLOCK,'id':'full','progression':4,'status':'burst'},'burst|progress')):
            with self.assertRaisesRegex(ValueError,message):install(self.c,[bad],self.chronology)
        with self.c.transaction() as con:self.assertEqual(len(WorldPlanner(con).clocks()),1)

    def test_first_chronicle_creates_secret_clock_progress_and_mandatory_sign(self):
        self.chronicle()
        self.planner.transform=lambda packet,response:{**response,'chronicle':{
            'clocks':[advance(packet)],'manifestations':[sign(packet)]}}
        result=self.loop.run_turn('Hello.')
        self.assertEqual(result['status'],'recorded',result)
        packet=self.planner.packets[0]
        self.assertEqual(packet['triggers'],['first_chronicle'])
        self.assertIn('chronology:raid',packet['allowed_references'])
        with self.c.transaction() as con:
            p=WorldPlanner(con)
            self.assertEqual(p.clocks()[0]['progression'],2)
            entry=Agenda(con).get('chronicle:rumor')
            self.assertEqual((entry['audience'],entry['interrupt'],entry['kind']),(['narrator'],False,'risk'))
            self.assertEqual(entry['references'],['chronicle:bandits:1'])
            self.assertEqual(p.reasons('Hello'),[])
            self.assertEqual(p.reasons('I wait three days.'),[])
        self.assertFalse(any(e['entry_id'].startswith('chronicle:') for e in self.c.world_agenda()['events']))

    def test_chronicle_rhythm_weekly_or_after_long_ellipsis(self):
        self.chronicle()
        self.loop.run_turn('Hello.')
        def wait(days):
            state=self.c.state()
            e=event(state,'Time passes.',[{'operation':'set','path':'/positions/player','value':'route'}],
                    minute=state['world_minute']+days*1440)
            self.assertEqual(self.c.apply(proposal(state,'Time passes.',[e]))['status'],'recorded')
        wait(2)
        with self.c.transaction() as con:
            self.assertEqual(WorldPlanner(con).reasons('Hello'),[])
            self.assertEqual(WorldPlanner(con).reasons('I wait three days.'),['long_ellipsis'])
        wait(5)
        with self.c.transaction() as con:self.assertEqual(WorldPlanner(con).reasons('Hello'),['periodic_chronicle'])

    def test_chronicle_validation_refusals_are_atomic(self):
        self.chronicle()
        cases=[
            (lambda p:{'clocks':[advance(p,3)],'manifestations':[]},'too fast'),
            (lambda p:{'clocks':[advance(p,3,status='burst')],'manifestations':[sign(p,kind='event')]},'too fast'),
            (lambda p:{'clocks':[],'manifestations':[sign(p,kind='event')]},'burst'),
            (lambda p:{'clocks':[],'manifestations':[sign(p,delay=8*1440)]},'seven days'),
            (lambda p:{'clocks':[],'manifestations':[sign(p,delay=0)]},'strictly future'),
            (lambda p:{'clocks':[{**advance(p),'id':'new','expected_revision':-1,'references':['exchange:1']}],
                       'manifestations':[]},'reference'),
            (lambda p:{'clocks':[{**advance(p),'id':f'new{i}','expected_revision':-1,'progression':0}
                                   for i in range(2)],'manifestations':[]},'new clock'),
            (lambda p:{'clocks':[{**advance(p),'segments':6}],'manifestations':[]},'segments'),
            (lambda p:{'clocks':[{**advance(p),'references':['exchange:1']}],
                       'manifestations':[]},'(?i)chronology'),
            (lambda p:{'clocks':[{**advance(p),'id':'new','expected_revision':-1,
                                   'references':[p['chronicle']['clocks'][0]['reference']]}],
                       'manifestations':[]},'(?i)chronology'),
        ]
        for build,message in cases:
            self.planner.transform=lambda packet,response,build=build:{**response,'chronicle':build(packet)}
            with self.assertRaisesRegex(ModelUnavailable,message):self.loop.run_turn('Hello.')
            with self.c.transaction() as con:
                self.assertEqual(WorldPlanner(con).clocks()[0]['progression'],1)
                self.assertIsNone(Agenda(con).get('chronicle:rumor'))
            with self.c.transaction() as con:
                con.execute("UPDATE planner_reviews SET status='replaced'")
                session=self.c._session(con);session.update(pending_action=None,phase='awaiting_player')
                self.c._save_session(con,session)

    def test_canonical_clock_retains_anchor_when_reusing_rejected_response(self):
        self.chronicle()
        def update(packet,response):
            item=advance(packet)
            item['references']=[packet['chronicle']['clocks'][0]['reference']]
            return {**response,'chronicle':{'clocks':[item],'manifestations':[]}}
        self.planner.transform=update
        # Reproduce an answer kept under the old validation rule.
        with patch.object(WorldPlanner,'_check_clock',side_effect=ValueError('Chronology reference missing')):
            with self.assertRaisesRegex(ModelUnavailable,'(?i)chronology'):
                self.loop.run_turn('Hello.')
        self.assertEqual(self.narrator.calls,0)
        result=self.loop.run_turn(resume=True)
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(self.planner.calls,1)
        with self.c.transaction() as con:
            clock=WorldPlanner(con).clocks()[0]
            self.assertEqual(clock['progression'],2)
            self.assertEqual(clock['references'],['chronicle:bandits:0','chronology:raid'])
            receipt=json.loads(con.execute('SELECT response FROM planner_reviews').fetchone()[0])
            self.assertEqual(receipt['chronicle']['clocks'][0]['references'],['chronicle:bandits:0'])

    def test_full_clock_bursts_into_interrupting_event(self):
        self.chronicle({**CLOCK,'progression':3})
        def burst(packet,response):
            return {**response,'chronicle':{'clocks':[advance(packet,status='burst')],
                                            'manifestations':[sign(packet,'blockade','event')]}}
        self.planner.transform=burst
        self.assertEqual(self.loop.run_turn('Hello.')['status'],'recorded')
        with self.c.transaction() as con:
            self.assertEqual(WorldPlanner(con).clocks()[0]['status'],'burst')
            entry=Agenda(con).get('chronicle:blockade')
            self.assertTrue(entry['interrupt']);self.assertTrue(entry['reason'].startswith('Big event'))

    def test_burst_clock_without_active_event_can_be_reopened_once(self):
        self.chronicle({**CLOCK,'progression':3})
        self.planner.transform=lambda packet,response:{**response,'chronicle':{
            'clocks':[advance(packet,status='burst')],'manifestations':[sign(packet,'blockade','event')]}}
        self.assertEqual(self.loop.run_turn('Hello.')['status'],'recorded')
        with self.c.transaction() as con:
            # The deadline is closed as a mere sign: the clock stays burst with no active event.
            con.execute("UPDATE world_agenda SET status='finished' WHERE entry_id='chronicle:blockade'")
            packet=self.planner.packets[-1]
            p=WorldPlanner(con)
            ok=p._reopenable({c['id']:c for c in p.clocks()},{c['id']:c for c in p.clocks()},packet)
            self.assertEqual(ok,{'bandits'})
            con.execute("UPDATE world_agenda SET status='planned' WHERE entry_id='chronicle:blockade'")
            self.assertEqual(p._reopenable({c['id']:c for c in p.clocks()},{c['id']:c for c in p.clocks()},packet),set())

    def test_burst_without_event_refused(self):
        self.chronicle({**CLOCK,'progression':3})
        self.planner.transform=lambda packet,response:{**response,'chronicle':{
            'clocks':[advance(packet,status='burst')],'manifestations':[]}}
        with self.assertRaisesRegex(ModelUnavailable,'big event'):self.loop.run_turn('Hello.')

    def manifestation_case(self):
        self.chronicle()
        self.planner.transform=lambda packet,response:{**response,'chronicle':{
            'clocks':[advance(packet)],'manifestations':[sign(packet)]}}
        self.loop.run_turn('Hello.')
        prepared=self.c.prepare_turn('J’attends ten minutes.',narrative_memory=True)
        entry=next(e for e in prepared['world_agenda']['events'] if e['entry_id']=='chronicle:rumor')
        state=prepared['state']
        outcome=event(state,'The guard reports the rumor of attacked convoys.',[],minute=entry['due_minute'])
        outcome['causes']=['agenda:chronicle:rumor']
        update={k:v for k,v in entry.items() if k not in ('revision','reference','date')}
        update.update(expected_revision=entry['revision'],status='finished',outcome_event_index=0)
        p=proposal(state,'The guard reports the rumor.',[outcome])
        return prepared,p,update,outcome

    def check(self,prepared,p,*updates):
        with self.c.transaction() as con:
            Agenda(con).validate(p,list(updates),None,[],{'agenda:chronicle:rumor','chronicle:bandits:1'},
                                 prepared['state'],prepared['world_agenda'])

    def test_narrator_must_show_chronicle_manifestation_to_player(self):
        prepared,p,update,outcome=self.manifestation_case()
        outcome['audience']=['narrator']
        with self.assertRaisesRegex(ValueError,'not perceived|unauthorized audience'):self.check(prepared,p,update)
        outcome['audience']=['narrator','player']
        self.check(prepared,p,update)
        update['status']='cancelled'
        with self.assertRaisesRegex(ValueError,'cancelled'):self.check(prepared,p,update)

    def test_narrator_forgetting_chronicle_manifestation_is_refused(self):
        prepared,p,update,outcome=self.manifestation_case()
        with self.c.transaction() as con:
            with self.assertRaisesRegex(ValueError,'Forgotten deadline'):
                Agenda(con).validate(p,[],None,[],set(),prepared['state'],prepared['world_agenda'])

    def test_chronicle_report_is_bounded(self):
        prepared,p,update,outcome=self.manifestation_case()
        outcome['audience']=['narrator']
        update.update(status='postponed',due_minute=update['due_minute']+3*1440)
        with self.assertRaisesRegex(ValueError,'two days'):self.check(prepared,p,update)
        update['due_minute']=outcome['world_minute']+60
        self.check(prepared,p,update)
        with self.c.transaction() as con:
            entry=Agenda(con).get('chronicle:rumor')
            for revision in (1,2):
                Agenda(con)._put({**{k:v for k,v in entry.items() if k!='revision'},'status':'postponed'},'test',0,revision)
        update['expected_revision']=2
        with self.assertRaisesRegex(ValueError,'twice'):self.check(prepared,p,update)

    def test_narrator_cannot_create_chronicle_entries(self):
        prepared,p,update,outcome=self.manifestation_case()
        outcome['audience']=['narrator','player']
        created={**update,'entry_id':'chronicle:invented','expected_revision':-1,'status':'planned',
                 'due_minute':outcome['world_minute']+60,'outcome_event_index':None}
        with self.assertRaisesRegex(ValueError,'chronicler'):self.check(prepared,p,update,created)

    def test_planner_preparation_never_writes_game_state(self):
        self.chronicle()
        prepared=self.c.prepare_turn('I wait a month.',narrative_memory=True)
        self.planner.transform=lambda packet,response:{**response,'chronicle':{
            'clocks':[advance(packet)],'manifestations':[sign(packet)]}}
        with self.c.transaction() as con:before=json.loads(con.execute('SELECT state FROM campaign').fetchone()[0])
        review_world(self.c,self.planner,prepared)
        self.assertEqual(self.c.state(),before)
        self.assertEqual(self.c.resume()['session']['pending_action']['text'],'I wait a month.')

    def test_changed_agenda_without_state_change_refuses_chronicle(self):
        self.chronicle()
        with self.c.transaction() as con:
            item={'entry_id':'test:date','expected_revision':-1,'title':'Suivi','actor':'guard','location':'gate',
                'kind':'verification','due_minute':20,'conditions':[],'references':['exchange:1'],
                'audience':['narrator'],'interrupt':False,'status':'planned','reason':'Check.','outcome_event_index':None}
            Agenda(con)._put(item,'test',0)
        def change(packet,response):
            with self.c.transaction() as con:
                Agenda(con)._put(item,'concurrent',0,1)
            return response
        self.planner.transform=change
        with self.assertRaisesRegex(ModelUnavailable,'Agenda modified'):self.loop.run_turn('Hello.')
        self.assertEqual(self.narrator.calls,0)

    def test_noop_intention_does_not_count_as_substantial(self):
        self.loop.run_turn('Hello.')
        state=self.c.state()
        e=event(state,'The player specifies their goal.',[{'operation':'set','path':'/intentions/player','value':'Wait for an answer.'}])
        self.c.apply(proposal(state,'The conversation continues.',[e]))
        with self.c.transaction() as con:self.assertEqual(con.execute('SELECT turn_count FROM planner_meta').fetchone()[0],0)


if __name__=='__main__':unittest.main(verbosity=2)
