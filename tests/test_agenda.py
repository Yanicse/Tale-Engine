"""Agenda rules on a fresh demonstration campaign: deadlines, early closure, interruption, visibility, replay, rolls."""
from contextlib import closing, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from novel_engine.adapters import ModelUnavailable
from novel_engine.agenda import Agenda, date, PACKET_LIMIT
from novel_engine.campaign import Campaign
from novel_engine.demo import initial_state, configuration
from novel_engine.narration_codex import normalize_response, response_schema
from novel_engine.narrative import NarrativeLoop
from novel_engine.scripts.play_narrative import main
from novel_engine.scripts.verify_game_kit import check_schema


class Narrator:
    def __init__(self,response):self.response=response;self.calls=0;self.last_metrics={}
    def generate(self,prompt):self.calls+=1;self.prompt=prompt;return deepcopy(self.response)


class AgendaTests(unittest.TestCase):
    ACTION='I ask the harbor master for news.'
    LONG_ACTIVITY='I train until something interrupts me.'

    def setUp(self):
        folder=ROOT/'.tmp-tests';folder.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=folder)
        self.path=Path(self.temp.name)/'agenda.sqlite'
        state=initial_state('agenda_test');state['world_minute']=1080
        self.c=Campaign(self.path).create(state,configuration())
        self.c.open_scene('The courier waits at the harbor.')
        self.state=self.c.state();self.now=self.state['world_minute']
        self.reference='demo:initial_configuration'
        with self.c.transaction() as con:Agenda(con).initialize()

    def tearDown(self):self.temp.cleanup()

    def entry(self, identifier='courier', after=60, interrupt=False, secret=False, kind='appointment'):
        return {'entry_id':identifier,'expected_revision':-1,'title':'Test deadline: '+identifier,
            'actor':'Mira' if secret else 'guild','location':'harbor',
            'kind':kind,'due_minute':self.now+after,'conditions':[],
            'references':[self.reference],'audience':['narrator','npc:mira'] if secret else ['narrator','player'],
            'interrupt':interrupt,'status':'planned','reason':'Scenario isolated on a fresh campaign.',
            'outcome_event_index':None}

    def put(self, entry):
        with self.c.transaction() as con:Agenda(con)._put(entry,'test:preparation',self.now)
        return entry

    def event(self, minute, cause=None, secret=False):
        return {'world_minute':minute,'description':'Step of the test scenario.',
            'causes':[cause or self.reference],'conditions':[],
            'changes':[{'operation':'set','path':'/intentions/test_agenda',
                        'value_json':json.dumps('Step '+str(minute))}],
            'audience':['narrator','npc:mira'] if secret else ['narrator','player']}

    def resolved(self, entry, index=0, status='done', delay=None):
        update=deepcopy(entry);update.update(expected_revision=0,status=status,outcome_event_index=index)
        if delay is not None:update['due_minute']=delay
        update['reason']='Reasoned resolution of the scenario.'
        return update

    def response(self, events=(), updates=(), continuation=None, rolls=()):
        return {'narrative_schema_version':'1.1','narration':'Isolated test resolution.',
            'events':list(events),'blocking_questions':[],'memory_request':None,
            'memory_updates':[],'agenda_updates':list(updates),'continuation':continuation,'roll_checks':list(rolls)}

    def run_response(self,response,retry=False):
        narrator=Narrator(response);loop=NarrativeLoop(self.c,narrator)
        pending=self.c.resume()['session']['pending_action']
        result=loop.run_turn(resume=True,retry=retry) if pending else loop.run_turn(self.ACTION)
        return result,loop,narrator

    def open_agreement(self, key='lodging'):
        with self.c.transaction() as con:
            state=self.c._state(con)
            state['commitments'][key]={'description':'The harbor master will confirm the lodging.','status':'open',
                'references':[self.reference],'audience':['narrator','player']}
            con.execute('UPDATE campaign SET state=? WHERE id=1',(json.dumps(state),))
        self.state=self.c.state()

    def test_schema_and_legacy_readability(self):
        check_schema(response_schema())
        legacy={'narration':'The guard greets you.','events':[],'blocking_questions':[]}
        normalized=normalize_response(legacy)
        self.assertEqual(normalized['narrative_schema_version'],'1.1')
        self.assertEqual(normalized['agenda_updates'],[])

    def test_new_open_commitment_gets_dated_followup(self):
        self.open_agreement()
        self.c.prepare_turn(narrative_memory=True)
        with self.c.transaction() as con:
            entry=Agenda(con).get('accord:lodging')
        self.assertEqual(entry['due_minute'],self.now+1440)
        self.assertEqual(entry['kind'],'verification')

    def test_month_skip_cannot_ignore_letter(self):
        self.put(self.entry())
        with self.assertRaisesRegex(ModelUnavailable,'Forgotten deadline'):
            self.run_response(self.response([self.event(self.now+30*1440)]))
        self.assertEqual(self.c.state(),self.state)
        self.assertIsNotNone(self.c.resume()['session']['pending_action'])

    def test_postponed_routine_forgotten_stays_due_instead_of_rejecting(self):
        self.put(self.entry(kind='project'))
        with self.c.transaction() as con:
            con.execute("UPDATE world_agenda SET status='postponed' WHERE entry_id='courier'")
        result,loop,narrator=self.run_response(self.response([self.event(self.now+300)]))
        self.assertEqual(result['status'],'recorded')
        with self.c.transaction() as con:
            self.assertEqual(Agenda(con).get('courier')['revision'],0)
            self.assertEqual(con.execute("SELECT status FROM world_agenda WHERE entry_id='courier'").fetchone()[0],'postponed')

    def run_long(self,events):
        self.c.begin_action(self.LONG_ACTIVITY)
        return self.run_response(self.response(events))

    def test_long_activity_leaves_quiet_followup_due(self):
        self.put(self.entry(kind='project'))
        result,_,_=self.run_long([self.event(self.now+300)])
        self.assertEqual(result['status'],'recorded')
        with self.c.transaction() as con:
            late=Agenda(con).get('courier')
            self.assertEqual((late['status'],late['revision'],late['due_minute']),('planned',0,self.now+60))

    def test_long_activity_still_stops_for_interruption_or_appointment(self):
        for kwargs in ({'kind':'project','interrupt':True},{'kind':'appointment'}):
            with self.subTest(kwargs=kwargs):
                with self.c.transaction() as con:
                    for table in ('world_agenda','agenda_history'):con.execute('DELETE FROM '+table)
                    session=self.c._session(con);session.update(phase='awaiting_player',pending_action=None)
                    self.c._save_session(con,session)
                self.put(self.entry(**kwargs))
                self.c.begin_action(self.LONG_ACTIVITY)
                with self.assertRaisesRegex(ModelUnavailable,'Forgotten deadline'):
                    self.run_response(self.response([self.event(self.now+300)]))

    def test_ordinary_action_still_refuses_forgotten_quiet_followup(self):
        self.put(self.entry(kind='project'))
        with self.assertRaisesRegex(ModelUnavailable,'Forgotten deadline'):
            self.run_response(self.response([self.event(self.now+300)]))

    def test_direct_engine_cannot_bypass_deadline(self):
        self.put(self.entry())
        pending=self.c.begin_action(self.ACTION)
        proposal={'schema_version':'0.2','request_id':pending['request_id'],
            'campaign_id':self.state['campaign_id'],'expected_state_version':self.state['state_version'],
            'narration':'Test on a fresh campaign.','blocking_questions':[],'events':[{
                'event_id':'test:skip','world_minute':self.now+300,'description':'Test.',
                'causes':[self.reference],'conditions':[],
                'changes':[{'operation':'set','path':'/intentions/test','value':'wait'}],
                'audience':['narrator','player']}]}
        result=self.c.apply(proposal)
        self.assertEqual(result['status'],'rejected')
        self.assertIn('Forgotten deadline',result['reason'])
        self.assertEqual(self.c.state(),self.state)

    def test_deadline_resolved_at_its_date_then_routine_can_continue(self):
        entry=self.put(self.entry())
        events=[self.event(entry['due_minute'],'agenda:courier'),self.event(self.now+300)]
        result,_,narrator=self.run_response(self.response(events,[self.resolved(entry)]))
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(result['world_minute_after'],self.now+300)
        self.assertEqual(narrator.calls,1)
        with self.c.transaction() as con:self.assertEqual(Agenda(con).get('courier')['status'],'done')

    def test_late_resolution_refused(self):
        entry=self.put(self.entry())
        with self.assertRaisesRegex(ModelUnavailable,'at its date'):
            self.run_response(self.response([self.event(self.now+300,'agenda:courier')],[self.resolved(entry)]))

    def test_new_future_project_can_reference_its_creation_event(self):
        entry=self.entry(kind='project',interrupt=True)
        entry.update(outcome_event_index=0,references=['current_event:0'])
        response=self.response([self.event(self.now+10)],[entry])
        response['narrative_schema_version']='1.2'
        creation_keys=('title','actor','location','kind','conditions','audience','interrupt')
        response['agenda_updates']=[{
            **{key:entry[key] for key in ('entry_id','status','reason','references','outcome_event_index','due_minute')},
            'creation':{key:entry[key] for key in creation_keys}}]
        result,_,narrator=self.run_response(response)
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(result['world_minute_after'],self.now+10)
        self.assertEqual(narrator.calls,1)
        with self.c.transaction() as con:
            saved=Agenda(con).get(entry['entry_id'])
        self.assertEqual(saved['status'],'planned')
        self.assertEqual(saved['due_minute'],entry['due_minute'])

    def test_legacy_future_entry_can_reference_its_creation_event(self):
        entry=self.entry();entry['outcome_event_index']=0
        result,_,_=self.run_response(self.response([self.event(self.now+10)],[entry]))
        self.assertEqual(result['status'],'recorded')

    def test_new_resolved_entry_still_requires_its_exact_date(self):
        event={**self.event(self.now+10),'event_id':'test:resolution'}
        proposal={'events':[event],'blocking_questions':[],'request_id':'test:date'}
        for status in ('done','finished','cancelled'):
            with self.subTest(status=status), self.c.transaction() as con:
                entry=self.entry();entry.update(status=status,outcome_event_index=0)
                with self.assertRaisesRegex(ValueError,'must respect its fictional date'):
                    Agenda(con).validate(proposal,[entry],None,[],{self.reference},self.state,{'events':[]})

    def test_new_planned_entry_still_requires_a_future_date(self):
        entry=self.entry(after=10);entry['outcome_event_index']=0
        with self.assertRaisesRegex(ModelUnavailable,'strictly in the future'):
            self.run_response(self.response([self.event(self.now+10)],[entry]))

    def test_voluntary_verification_closes_early_and_keeps_planned_date(self):
        entry=self.put(self.entry(kind='verification'))
        event=self.event(self.now+10,'agenda:courier');event['causes'].append('current_action')
        response=self.response([event],[self.resolved(entry,status='finished',delay=self.now+10)])
        original=deepcopy(response)
        result,loop,narrator=self.run_response(response)
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(result['world_minute_after'],self.now+10)
        self.assertEqual(narrator.calls,1)
        self.assertEqual(response,original)
        self.assertEqual(loop.last_metrics['agenda_date_corrections'],[{
            'entry_id':'courier','due_minute_before':self.now+10,
            'due_minute_after':entry['due_minute'],'resolution_minute':self.now+10}])
        with self.c.transaction() as con:
            saved=Agenda(con).get('courier')
            diagnostic=json.loads(con.execute('SELECT payload FROM narrative_diagnostics ORDER BY id DESC LIMIT 1').fetchone()[0])
        self.assertEqual(saved['due_minute'],entry['due_minute'])
        self.assertEqual(saved['status'],'finished')
        self.assertEqual(diagnostic['response']['agenda_updates'][0]['due_minute'],self.now+10)
        self.assertEqual(diagnostic['agenda_date_corrections'],loop.last_metrics['agenda_date_corrections'])

    def test_early_closure_can_precede_routine_crossing_original_date(self):
        entry=self.put(self.entry(kind='verification'))
        first=self.event(self.now+10,'agenda:courier');first['causes'].append('current_action')
        result,_,_=self.run_response(self.response([first,self.event(self.now+300)],
            [self.resolved(entry,status='finished')]))
        self.assertEqual(result['status'],'recorded')

    def test_early_closure_requires_voluntary_action_and_verification(self):
        original=self.c.path
        for index,(kind,interrupt,status,voluntary) in enumerate((
            ('appointment',False,'finished',True),('project',False,'finished',True),
            ('risk',False,'finished',True),('verification',True,'finished',True),
            ('verification',False,'done',True),('verification',False,'finished',False))):
            with self.subTest(kind=kind,interrupt=interrupt,status=status,voluntary=voluntary):
                # Each refusal is an independent scenario, not six generations of the same action.
                isolated=original.with_name('scenario_'+str(index)+'.sqlite')
                with closing(sqlite3.connect(original)) as source, closing(sqlite3.connect(isolated)) as target:
                    source.backup(target)
                self.c=Campaign(isolated)
                entry=self.put(self.entry('verification_test:'+str(index),kind=kind,interrupt=interrupt))
                event=self.event(self.now+10,'agenda:'+entry['entry_id'])
                if voluntary:event['causes'].append('current_action')
                with self.assertRaisesRegex(ModelUnavailable,'before its deadline'):
                    self.run_response(self.response([event],[self.resolved(entry,status=status)]))
                self.assertEqual(self.c.state(),self.state)

    def test_early_closure_does_not_accept_kind_change_or_new_interrupt(self):
        entry=self.put(self.entry(kind='project'))
        event=self.event(self.now+10,'agenda:courier');event['causes'].append('current_action')
        update=self.resolved(entry,status='finished');update['kind']='verification'
        with self.assertRaisesRegex(ModelUnavailable,'before its deadline'):
            self.run_response(self.response([event],[update]))
        entry=self.put(self.entry('verification_with_interruption',kind='verification'))
        event=self.event(self.now+10,'agenda:'+entry['entry_id']);event['causes'].append('current_action')
        update=self.resolved(entry,status='finished');update['interrupt']=True
        with self.assertRaisesRegex(ModelUnavailable,'before its deadline'):
            self.run_response(self.response([event],[update]),retry=True)

    def test_early_closure_cannot_bypass_conditions(self):
        entry=self.entry(kind='verification')
        entry['conditions']=[{'path':'/resources/player_gold','equals':999999}]
        self.put(entry)
        event=self.event(self.now+10,'agenda:courier');event['causes'].append('current_action')
        update=self.resolved(entry,status='finished')
        update['conditions']=[{'path':'/resources/player_gold','equals_json':'999999'}]
        with self.assertRaisesRegex(ModelUnavailable,'Project conditions not met'):
            self.run_response(self.response([event],[update]))

    def test_early_closure_cannot_terminate_open_commitment(self):
        self.open_agreement()
        entry=self.put(self.entry('accord:lodging',kind='verification'))
        event=self.event(self.now+10,'agenda:'+entry['entry_id']);event['causes'].append('current_action')
        with self.assertRaisesRegex(ModelUnavailable,'still open'):
            self.run_response(self.response([event],[self.resolved(entry,status='finished')]))

    def test_arbitrary_resolution_date_change_still_refused(self):
        entry=self.put(self.entry(kind='verification'))
        event=self.event(self.now+10,'agenda:courier');event['causes'].append('current_action')
        with self.assertRaisesRegex(ModelUnavailable,'Answer received but refused by the local validation.*keeps the date'):
            self.run_response(self.response([event],[self.resolved(entry,status='finished',delay=self.now+20)]))

    def test_late_event_cannot_be_repaired_by_date_normalization(self):
        entry=self.put(self.entry())
        with self.assertRaisesRegex(ModelUnavailable,'keeps the date'):
            self.run_response(self.response([self.event(self.now+300,'agenda:courier')],
                [self.resolved(entry,delay=self.now+300)]))

    def test_overdue_checkpoint_keeps_old_date_and_resolves_now(self):
        entry=self.put(self.entry(after=-10))
        result,loop,_=self.run_response(self.response([self.event(self.now,'agenda:courier')],
            [self.resolved(entry,delay=self.now)]))
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(loop.last_metrics['agenda_date_corrections'][0]['due_minute_after'],self.now-10)

    def test_engine_keeps_strict_date_check_for_direct_proposals(self):
        entry=self.put(self.entry(kind='verification'))
        event=self.event(self.now+10,'agenda:courier');event['causes'].append('current_action')
        self.c.begin_action(self.ACTION);prepared=self.c.prepare_turn(narrative_memory=True)
        loop=NarrativeLoop(self.c,None)
        proposal=loop._proposal(prepared,self.response([event],[self.resolved(entry,status='finished')]))
        changes=deepcopy(loop._agenda_changes);changes['updates'][0]['due_minute']=self.now+10
        result=self.c.apply(proposal,agenda_changes=changes)
        self.assertEqual(result['status'],'rejected')
        self.assertIn('keeps the date',result['reason'])
        self.assertEqual(self.c.state(),self.state)

    def test_date_corrections_survive_prepared_response_resume(self):
        entry=self.put(self.entry(kind='verification'))
        event=self.event(self.now+10,'agenda:courier');event['causes'].append('current_action')
        narrator=Narrator(self.response([event],[self.resolved(entry,status='finished',delay=self.now+10)]))
        loop=NarrativeLoop(self.c,narrator)
        with patch.object(self.c,'apply',side_effect=OSError('Simulated interruption')):
            with self.assertRaises(OSError):loop.run_turn(self.ACTION)
        audit=deepcopy(loop.last_metrics['agenda_date_corrections'])
        result=loop.run_turn(resume=True)
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(narrator.calls,1)
        self.assertEqual(loop.last_metrics['model_calls'],0)
        self.assertEqual(loop.last_metrics['agenda_date_corrections'],audit)

    def test_interrupt_stops_at_deadline_and_preserves_goal(self):
        entry=self.put(self.entry('attack',interrupt=True))
        goal={'goal':'Work on the house','remaining':'Finish the working day',
              'status':'suspended','event_index':0}
        result,_,_=self.run_response(self.response([self.event(entry['due_minute'],'agenda:attack')],
                                                  [self.resolved(entry)],goal))
        self.assertEqual(result['world_minute_after'],entry['due_minute'])
        prepared=self.c.prepare_turn(narrative_memory=True)
        self.assertEqual(prepared['world_agenda']['interrupted_goal']['goal'],goal['goal'])

    def test_skip_beyond_interruption_refused(self):
        entry=self.put(self.entry('attack',interrupt=True))
        with self.assertRaisesRegex(ModelUnavailable,'goes past an interruption'):
            self.run_response(self.response([self.event(entry['due_minute'],'agenda:attack'),self.event(self.now+300)],
                                           [self.resolved(entry)]))

    def test_interruption_requires_saved_goal(self):
        entry=self.put(self.entry('attack',interrupt=True))
        with self.assertRaisesRegex(ModelUnavailable,"must keep the player's goal.*goal.*event_index.*continuation=null"):
            self.run_response(self.response([self.event(entry['due_minute'],'agenda:attack')],[self.resolved(entry)]))

    def test_expected_arrival_can_complete_goal_at_interruption(self):
        entry=self.put(self.entry('arrival',interrupt=True))
        goal={'goal':'Wait for an arrival','remaining':'Nothing left to wait for.',
              'status':'finished','event_index':None}
        result,_,_=self.run_response(self.response([self.event(entry['due_minute'],'agenda:arrival')],
                                                  [self.resolved(entry)],goal))
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(result['world_minute_after'],entry['due_minute'])
        self.assertIsNone(self.c.prepare_turn()['world_agenda']['interrupted_goal'])

    def test_unplanned_interruption_can_save_goal(self):
        goal={'goal':'Work','remaining':'Finish the work','status':'suspended','event_index':0}
        result,_,_=self.run_response(self.response([self.event(self.now+30)],continuation=goal))
        self.assertEqual(result['status'],'recorded')

    def test_unrelated_dialogue_preserves_suspended_goal(self):
        goal={'goal':'Work','remaining':'Finish','status':'suspended','event_index':0}
        self.run_response(self.response([self.event(self.now+30)],continuation=goal))
        self.run_response(self.response())
        self.assertEqual(self.c.prepare_turn()['world_agenda']['interrupted_goal']['goal'],'Work')
        done={**goal,'status':'finished','event_index':None}
        self.run_response(self.response(continuation=done))
        self.assertIsNone(self.c.prepare_turn()['world_agenda']['interrupted_goal'])

    def test_suspended_goal_without_visible_event_becomes_no_continuation(self):
        # Remainder of a routine or stop at a horizon: not an interruption, so no goal is kept.
        goal={'goal':'Five-day routine','remaining':'Continue the routine','status':'suspended','event_index':None}
        result,loop,_=self.run_response(self.response([self.event(self.now+30)],continuation=goal))
        self.assertEqual(result['status'],'recorded')
        self.assertIsNone(self.c.prepare_turn()['world_agenda']['interrupted_goal'])
        self.assertEqual(loop.last_metrics['continuation_corrections'][0]['reason'],'suspended_without_perceivable_event')

    def test_suspended_goal_pointing_to_private_event_is_still_refused_with_guidance(self):
        goal={'goal':'Work','remaining':'Finish','status':'suspended','event_index':0}
        with self.assertRaisesRegex(ModelUnavailable,'continuation=null'):
            self.run_response(self.response([self.event(self.now+30,secret=True)],continuation=goal))

    def test_secret_project_moves_without_exposing_it_and_schedules_next_step(self):
        entry=self.put(self.entry('mira:test',secret=True,kind='project'))
        next_step=self.entry('mira:next',after=300,secret=True,kind='project')
        next_step['references']=['current_event:0']
        result,_,_=self.run_response(self.response([self.event(entry['due_minute'],'agenda:mira:test',True),
                                                   self.event(self.now+120)],
                                                  [self.resolved(entry),next_step]))
        self.assertEqual(result['world_minute_after'],self.now+120)
        public=self.c.world_agenda()
        self.assertNotIn('mira',json.dumps(public))
        with self.c.transaction() as con:self.assertIsNotNone(Agenda(con).get('mira:next'))

    def test_completed_project_requires_next_stage(self):
        entry=self.put(self.entry('mira:test',secret=True,kind='project'))
        with self.assertRaisesRegex(ModelUnavailable,'next step'):
            self.run_response(self.response([self.event(entry['due_minute'],'agenda:mira:test',True)],
                                           [self.resolved(entry)]))

    def test_event_changes_can_invalidate_project_conditions(self):
        entry=self.entry('attack')
        gold=self.state['resources']['player_gold']
        entry['conditions']=[{'path':'/resources/player_gold','equals':gold}]
        self.put(entry)
        before=self.event(self.now+10)
        before['changes']=[{'operation':'set','path':'/resources/player_gold','value_json':str(gold-1)}]
        update=self.resolved(entry,1)
        update['conditions']=[{'path':'/resources/player_gold','equals_json':str(gold)}]
        with self.assertRaisesRegex(ModelUnavailable,'Project conditions'):
            self.run_response(self.response([before,self.event(entry['due_minute'],'agenda:attack')],[update]))

    def test_redundant_future_project_can_be_cancelled_now_with_command_protocol(self):
        entry = self.put(self.entry('redundant_followup', after=60, kind='project'))
        event = self.event(self.now, 'agenda:redundant_followup')
        event.update(changes=[], description='The redundant follow-up is cancelled, without carrying out its future work.')
        update = {'entry_id': entry['entry_id'], 'status': 'cancelled',
                  'reason': 'Cancellation of the redundant follow-up on historical proof.',
                  'references': ['agenda:redundant_followup', self.reference],
                  'outcome_event_index': 0, 'due_minute': None, 'creation': None}
        response = self.response([event], [update])
        response['narrative_schema_version'] = '1.2'
        result, _, _ = self.run_response(response)
        self.assertEqual(result['status'], 'recorded', result)
        after = self.c.state()
        self.assertEqual(after['world_minute'], self.now)
        self.assertEqual(after['resources'], self.state['resources'])
        with self.c.transaction() as con:
            cancelled = Agenda(con).get(entry['entry_id'])
        self.assertEqual(cancelled['status'], 'cancelled')
        self.assertEqual(cancelled['due_minute'], entry['due_minute'])

    def test_conditionally_impossible_project_can_be_cancelled(self):
        entry=self.entry('attack')
        entry['conditions']=[{'path':'/resources/player_gold','equals':999999}]
        self.put(entry)
        update=self.resolved(entry,status='cancelled')
        update['conditions']=[{'path':'/resources/player_gold','equals_json':'999999'}]
        result,_,_=self.run_response(self.response([self.event(entry['due_minute'],'agenda:attack')],[update]))
        self.assertEqual(result['status'],'recorded')

    def test_delay_requires_reason_event_and_new_future_date(self):
        entry=self.put(self.entry())
        update=self.resolved(entry,status='postponed',delay=self.now+300)
        result,_,_=self.run_response(self.response([self.event(entry['due_minute'],'agenda:courier')],[update]))
        self.assertEqual(result['status'],'recorded')
        with self.c.transaction() as con:
            self.assertEqual(Agenda(con).get('courier')['due_minute'],self.now+300)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM agenda_history WHERE entry_id='courier'").fetchone()[0],2)

    def test_private_origin_is_restricted_without_exposing_it(self):
        self.put(self.entry('hidden',secret=True))
        notice=self.entry('notice',after=300)
        notice['references']=['agenda:hidden']
        response=self.response(updates=[notice]);original=deepcopy(response)
        result,loop,narrator=self.run_response(response)
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(narrator.calls,1)
        self.assertEqual(response,original)
        self.assertNotIn('notice',json.dumps(self.c.world_agenda()))
        with self.c.transaction() as con:
            self.assertEqual(Agenda(con).get('notice')['audience'],['narrator'])
            diagnostic=json.loads(con.execute('SELECT payload FROM narrative_diagnostics WHERE request_id=? ORDER BY id DESC LIMIT 1',
                                             (result['request_id'],)).fetchone()[0])
        self.assertEqual(diagnostic['response']['agenda_updates'][0]['audience'],['narrator','player'])
        self.assertEqual(diagnostic['agenda_audience_corrections'],loop.last_metrics['agenda_audience_corrections'])

    def test_offscreen_report_uses_private_outcome_and_keeps_agreement(self):
        self.open_agreement()
        entry=self.put(self.entry())
        event=self.event(entry['due_minute'],'agenda:courier');event['audience']=['narrator']
        update=self.resolved(entry,status='postponed',delay=self.now+300)
        update['references']=['agenda:courier','current_event:0']
        before=deepcopy(self.c.state()['commitments'])
        result,loop,_=self.run_response(self.response([event],[update]))
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(self.c.state()['commitments'],before)
        self.assertNotIn('courier',json.dumps(self.c.world_agenda()))
        self.assertEqual(loop.last_metrics['agenda_audience_corrections'][0]['audience_after'],['narrator'])

    def test_public_sources_keep_public_audience_without_correction(self):
        entry=self.put(self.entry())
        result,loop,_=self.run_response(self.response([self.event(entry['due_minute'],'agenda:courier')],
            [self.resolved(entry,status='postponed',delay=self.now+300)]))
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(loop.last_metrics['agenda_audience_corrections'],[])
        self.assertIn('courier',json.dumps(self.c.world_agenda()))

    def test_visibility_reduction_cannot_hide_public_resolution(self):
        self.put(self.entry('hidden',secret=True,after=500))
        entry=self.put(self.entry())
        update=self.resolved(entry,status='postponed',delay=self.now+300)
        update['references']=['agenda:hidden']
        with self.assertRaisesRegex(ModelUnavailable,'contradicts the resolution event'):
            self.run_response(self.response([self.event(entry['due_minute'],'agenda:courier')],[update]))
        self.assertEqual(self.c.state(),self.state)

    def test_visibility_reduction_cannot_repair_an_interruption(self):
        self.put(self.entry('hidden',secret=True))
        notice=self.entry('notice',after=300,interrupt=True)
        notice['references']=['agenda:hidden']
        with self.assertRaisesRegex(ModelUnavailable,'refused for an interruption'):
            self.run_response(self.response(updates=[notice]))
        self.assertEqual(self.c.state(),self.state)

    def test_intersection_uses_stored_events_and_memory_sources(self):
        from novel_engine.indexed_memory import MemoryIndex
        update=self.entry('notice',after=300)
        update['audience']=['narrator','player','npc:mira']
        update['references']=['test:stored','test:memory']
        with self.c.transaction() as con:
            MemoryIndex(con).sync()
            con.execute('INSERT INTO events VALUES (?,?,?)',('test:stored',self.state['state_version'],
                json.dumps({'audience':['narrator','player']})))
            memory_ref=con.execute('SELECT reference FROM memory_sources WHERE reference NOT IN (SELECT event_id FROM events) LIMIT 1').fetchone()[0]
            con.execute('UPDATE memory_sources SET audience=? WHERE reference=?',
                        (json.dumps(['narrator','npc:mira']),memory_ref))
            update['references'][1]=memory_ref
            normalized,corrections=Agenda(con).normalize_audiences({'events':[]},[update])
            self.assertEqual(normalized[0]['audience'],['narrator'])
            self.assertEqual(len(corrections[0]['limiting_sources']),2)
            again,audit=Agenda(con).normalize_audiences({'events':[]},normalized)
            self.assertEqual(again,normalized);self.assertEqual(audit,[])

    def test_normalization_cannot_add_missing_narrator(self):
        update=self.entry();update['audience']=['player'];update['references']=['canon:test']
        with self.c.transaction() as con:
            with self.assertRaisesRegex(ValueError,'narrator must stay authorized'):
                Agenda(con).normalize_audiences({'events':[]},[update])

    def test_existing_secret_entry_cannot_gain_recipients(self):
        entry=self.put(self.entry('hidden',secret=True))
        update=self.resolved(entry,status='postponed',delay=self.now+300)
        update['audience'].append('player')
        update['references']=['agenda:hidden']
        result,_,_=self.run_response(self.response([self.event(entry['due_minute'],'agenda:hidden',True)],[update]))
        self.assertEqual(result['status'],'recorded')
        with self.c.transaction() as con:
            self.assertEqual(Agenda(con).get('hidden')['audience'],['narrator','npc:mira'])
        self.assertNotIn('hidden',json.dumps(self.c.world_agenda()))

    def test_engine_still_rejects_unnormalized_private_source(self):
        self.put(self.entry('hidden',secret=True))
        notice=self.entry('notice',after=300);notice['references']=['agenda:hidden']
        prepared=self.c.prepare_turn(self.ACTION,narrative_memory=True)
        loop=NarrativeLoop(self.c,None)
        proposal=loop._proposal(prepared,self.response(updates=[notice]))
        loop._agenda_changes['updates'][0]['audience']=['narrator','player']
        result=self.c.apply(proposal,agenda_changes=loop._agenda_changes)
        self.assertEqual(result['status'],'rejected')
        self.assertIn('private source',result['reason'])
        self.assertEqual(self.c.state(),self.state)

    def test_corrected_response_replay_preserves_audit_without_new_generation(self):
        self.put(self.entry('hidden',secret=True))
        notice=self.entry('notice',after=300);notice['references']=['agenda:hidden']
        narrator=Narrator(self.response(updates=[notice]));loop=NarrativeLoop(self.c,narrator)
        with patch.object(self.c,'apply',side_effect=OSError('Simulated interruption')):
            with self.assertRaises(OSError):loop.run_turn(self.ACTION)
        audit=deepcopy(loop.last_metrics['agenda_audience_corrections'])
        result=loop.run_turn(resume=True)
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(narrator.calls,1)
        self.assertEqual(loop.last_metrics['model_calls'],0)
        self.assertEqual(loop.last_metrics['agenda_audience_corrections'],audit)

    def test_context_bound_rejects_skipping_unloaded_entries(self):
        for i in range(PACKET_LIMIT+1):self.put(self.entry('test:'+str(i),after=10+i))
        prepared=self.c.prepare_turn(narrative_memory=True)
        self.assertEqual(len(prepared['world_agenda']['events']),PACKET_LIMIT)
        with self.assertRaises(ModelUnavailable):
            self.run_response(self.response([self.event(self.now+300)]))

    def test_replayed_response_updates_agenda_exactly_once(self):
        entry=self.put(self.entry())
        narrator=Narrator(self.response([self.event(entry['due_minute'],'agenda:courier')],[self.resolved(entry)]))
        loop=NarrativeLoop(self.c,narrator)
        with patch.object(self.c,'apply',side_effect=OSError('Simulated interruption')):
            with self.assertRaises(OSError):loop.run_turn(self.ACTION)
        result=loop.run_turn(resume=True)
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(narrator.calls,1)
        with self.c.transaction() as con:
            self.assertEqual(con.execute("SELECT revision FROM world_agenda WHERE entry_id='courier'").fetchone()[0],1)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM agenda_receipts').fetchone()[0],1)

    def test_agenda_failure_rolls_back_world_and_replay_needs_no_new_generation(self):
        entry=self.put(self.entry())
        narrator=Narrator(self.response([self.event(entry['due_minute'],'agenda:courier')],[self.resolved(entry)]))
        loop=NarrativeLoop(self.c,narrator)
        with patch.object(Agenda,'apply',side_effect=ValueError('Simulated save error')):
            with self.assertRaises(ValueError):loop.run_turn(self.ACTION)
        self.assertEqual(self.c.state(),self.state)
        with self.c.transaction() as con:self.assertEqual(Agenda(con).get('courier')['revision'],0)
        self.assertEqual(loop.run_turn(resume=True)['status'],'recorded')
        self.assertEqual(narrator.calls,1)

    def test_roll_is_stable_after_retry_and_reevaluation(self):
        pending=self.c.begin_action(self.ACTION)
        first=self.c.prepare_turn()['world_agenda']['d100_rolls']
        self.assertEqual(first,self.c.prepare_turn()['world_agenda']['d100_rolls'])
        proposal=self.c.prepare_turn()['model_proposal']
        # The fresh campaign is at version 0, so the stale version is simulated with a different one.
        proposal['expected_state_version']+=1
        self.assertEqual(self.c.apply(proposal)['status'],'rejected')
        self.c.reevaluate_action()
        self.assertEqual(first,self.c.prepare_turn()['world_agenda']['d100_rolls'])
        self.assertNotEqual(pending['request_id'],self.c.resume()['session']['pending_action']['request_id'])

    def test_roll_result_is_validated_and_recorded(self):
        self.c.begin_action(self.ACTION)
        value=self.c.prepare_turn()['world_agenda']['d100_rolls']['general']
        roll={'key':'general','threshold':50,'success':value<=50,'reason':'Uncertainty of the scenario.'}
        result,_,_=self.run_response(self.response(rolls=[roll]))
        self.assertEqual(result['status'],'recorded')
        with self.c.transaction() as con:
            receipt=json.loads(con.execute('SELECT payload FROM agenda_receipts WHERE request_id=?',
                                         (result['request_id'],)).fetchone()[0])
            self.assertEqual(receipt['roll_checks'],[roll])

    def test_bad_roll_rejected(self):
        self.c.begin_action(self.ACTION)
        value=self.c.prepare_turn()['world_agenda']['d100_rolls']['general']
        roll={'key':'general','threshold':50,'success':not(value<=50),'reason':'Test.'}
        with self.assertRaisesRegex(ModelUnavailable,'Roll result incompatible'):
            self.run_response(self.response(rolls=[roll]))

    def test_local_agenda_command_no_model_time_or_secrets(self):
        self.put(self.entry('hidden',secret=True))
        self.put(self.entry('notice'))
        before=self.c.resume();out=io.StringIO()
        with patch('novel_engine.narration_codex.CodexNarrator') as narrator, redirect_stdout(out):
            self.assertEqual(main(['--campaign',str(self.path),'--agenda','--json']),0)
        narrator.assert_not_called()
        result=json.loads(out.getvalue())
        self.assertNotIn('hidden',json.dumps(result))
        self.assertEqual(self.c.resume(),before)

    def test_wall_clock_and_restart_do_not_advance_world(self):
        self.put(self.entry())
        before=self.c.resume()
        with patch('time.time',return_value=999999999999):self.c.world_agenda()
        self.assertEqual(Campaign(self.path).resume(),before)
        self.assertEqual(date(1440),'D1, T0 + 00 h 00')

    def test_secret_attack_becomes_observable_only_at_its_manifestation(self):
        entry=self.put(self.entry('hidden_attack',interrupt=True,secret=True))
        self.assertNotIn('hidden_attack',json.dumps(self.c.world_agenda()))
        goal={'goal':'Build','remaining':'Finish the work','status':'suspended','event_index':0}
        result,_,_=self.run_response(self.response(
            [self.event(entry['due_minute'],'agenda:hidden_attack')],[self.resolved(entry)],goal))
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(result['world_minute_after'],entry['due_minute'])
        with self.c.transaction() as con:
            self.assertNotIn('player',Agenda(con).get('hidden_attack')['audience'])

    def test_multiple_offscreen_stages_in_one_long_routine(self):
        first=self.put(self.entry('mira:departure',after=10,secret=True,kind='project'))
        second=self.entry('mira:inquiry',after=100,secret=True,kind='project')
        second.update(status='done',outcome_event_index=1,references=['current_event:0'])
        third=self.entry('mira:next',after=1000,secret=True,kind='project')
        third['references']=['current_event:1']
        events=[self.event(first['due_minute'],'agenda:mira:departure',True),
                self.event(second['due_minute'],'current_event:0',True),self.event(self.now+300)]
        result,_,narrator=self.run_response(self.response(events,[self.resolved(first),second,third]))
        self.assertEqual(result['world_minute_after'],self.now+300)
        self.assertEqual(narrator.calls,1)
        with self.c.transaction() as con:
            self.assertEqual(Agenda(con).get('mira:inquiry')['status'],'done')
            self.assertEqual(Agenda(con).get('mira:next')['status'],'planned')

    def test_check_of_pending_letter_requires_another_dated_step(self):
        entry=self.put(self.entry(kind='verification'))
        with self.assertRaisesRegex(ModelUnavailable,'next step'):
            self.run_response(self.response([self.event(entry['due_minute'],'agenda:courier')],[self.resolved(entry)]))

    def test_finished_dossier_can_close_with_recorded_outcome(self):
        entry=self.put(self.entry(kind='verification'))
        result,_,_=self.run_response(self.response([self.event(entry['due_minute'],'agenda:courier')],
                                                 [self.resolved(entry,status='finished')]))
        self.assertEqual(result['status'],'recorded')

    def test_open_commitment_cannot_silently_lose_its_followup(self):
        self.open_agreement()
        self.c.prepare_turn()
        with self.c.transaction() as con:
            entry=Agenda(con).get('accord:lodging')
            for row in con.execute("SELECT entry_id FROM world_agenda WHERE entry_id!='accord:lodging'").fetchall():
                other=Agenda(con).get(row['entry_id']);revision=other.pop('revision')
                other['due_minute']=self.now+3000
                Agenda(con)._put(other,'test:isolation',self.now,revision+1)
        entry.pop('revision')
        with self.assertRaisesRegex(ModelUnavailable,'still open'):
            self.run_response(self.response([self.event(entry['due_minute'],'agenda:accord:lodging')],
                                           [self.resolved(entry,status='finished')]))

    def test_revision_change_during_generation_preserves_world(self):
        entry=self.put(self.entry())
        narrator=Narrator(self.response([self.event(entry['due_minute'],'agenda:courier')],[self.resolved(entry)]))
        generate=narrator.generate
        def changed(prompt):
            with self.c.transaction() as con:
                changed_entry=deepcopy(entry);changed_entry['reason']='New information in the scenario.'
                Agenda(con)._put(changed_entry,'test:concurrent',self.now,1)
            return generate(prompt)
        with patch.object(narrator,'generate',side_effect=changed):
            with self.assertRaisesRegex(ModelUnavailable,'Obsolete agenda revision'):
                NarrativeLoop(self.c,narrator).run_turn(self.ACTION)
        self.assertEqual(self.c.state(),self.state)

    def test_unknown_origin_and_date_changes_are_rejected(self):
        entry=self.put(self.entry())
        update=self.resolved(entry);update['due_minute']+=1
        with self.assertRaisesRegex(ModelUnavailable,'date of its original deadline'):
            self.run_response(self.response([self.event(entry['due_minute'],'agenda:courier')],[update]))

    def test_future_same_turn_cause_is_refused(self):
        with self.assertRaisesRegex(ModelUnavailable,'Turn cause'):
            self.run_response(self.response([self.event(self.now+10,'current_event:1'),self.event(self.now+20)]))

    def test_public_agenda_does_not_expose_conditions_or_dice(self):
        entry=self.entry()
        entry['conditions']=[{'path':'/intentions/secret','equals':'Backstage plan'}]
        self.put(entry)
        public=self.c.world_agenda()
        text=json.dumps(public)
        self.assertNotIn('Backstage plan',text)
        self.assertEqual(public['d100_rolls'],{})

    def test_manual_cli_can_apply_agenda_in_same_transaction(self):
        from novel_engine.scripts.campaign_cli import main as manual_main
        entry=self.put(self.entry())
        prepared=self.c.prepare_turn(self.ACTION,narrative_memory=True)
        loop=NarrativeLoop(self.c,None)
        proposal=loop._proposal(prepared,self.response([self.event(entry['due_minute'],'agenda:courier')],
                                                      [self.resolved(entry)]))
        envelope={'proposal':proposal,'agenda_changes':loop._agenda_changes}
        out=io.StringIO()
        with patch('sys.argv',['campaign_cli','--campaign',str(self.path),'apply','--file','-','--short']), \
             patch('sys.stdin',io.StringIO(json.dumps(envelope))),redirect_stdout(out):
            manual_main()
        self.assertEqual(json.loads(out.getvalue())['status'],'recorded')
        with self.c.transaction() as con:self.assertEqual(Agenda(con).get('courier')['status'],'done')

    def test_conflicting_agenda_replay_is_rejected_without_changing_world(self):
        entry=self.put(self.entry())
        result,loop,_=self.run_response(self.response([self.event(entry['due_minute'],'agenda:courier')],
                                                     [self.resolved(entry)]))
        before=self.c.state()
        attempt=loop._previous_attempt(result['request_id']);stored=json.loads(attempt['proposal'])
        stored['agenda_changes']['updates'][0]['reason']='Other content after recording.'
        with self.assertRaisesRegex(ValueError,'Different agenda'):
            self.c.apply(stored['proposal'],agenda_changes=stored['agenda_changes'])
        self.assertEqual(self.c.state(),before)


if __name__=='__main__':unittest.main(verbosity=2)
