"""Contracts and complete journeys, with local CLI transport and real SQLite."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from novel_engine.adapters import ModelUnavailable
from novel_engine.agenda import Agenda
from novel_engine.campaign import Campaign
from novel_engine.contracts import load_schema
from novel_engine.demo import initial_state, configuration
from novel_engine.diagnostic_cli import collect_failure, failure_summary
from novel_engine.indexed_memory import MemoryIndex
from novel_engine.narrative import NarrativeLoop
from novel_engine.narration_codex import CodexNarrator, response_schema
from novel_engine.planner import CodexPlanner, WorldPlanner, planner_schema
from test_planner import install_test_chronicle
from novel_engine.schema_codex import compile_schema
from novel_engine.validation import validate
from novel_engine.scripts.play_narrative import main


class LocalNarrator(CodexNarrator):
    control = None
    def command(self, schema_path, workdir):
        args = super().command(schema_path, workdir)
        return [sys.executable, str(ROOT/'tests/fake_codex.py'), 'narrator', str(self.control), *args[1:]]


class LocalPlanner(CodexPlanner):
    control = None
    def command(self, schema_path, workdir):
        args = super().command(schema_path, workdir)
        return [sys.executable, str(ROOT/'tests/fake_codex.py'), 'planner', str(self.control), *args[1:]]


class CompleteLoopTests(unittest.TestCase):
    def setUp(self):
        base=ROOT/'.tmp-tests';base.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=base)
        self.c=Campaign(Path(self.temp.name)/'test.sqlite').create(initial_state('audit'),configuration())
        self.c.open_scene('You wait for a first step at the door.')
        self.control=Path(self.temp.name)/'check.txt'
        LocalNarrator.control=LocalPlanner.control=self.control
        with self.c.transaction() as con:
            Agenda(con).initialize(); MemoryIndex(con).sync()
            p=WorldPlanner(con);p.initialize();install_test_chronicle(con)

    def tearDown(self): self.temp.cleanup()

    def loop(self):
        return NarrativeLoop(self.c, LocalNarrator(executable='local'), planner=LocalPlanner(executable='local'))

    def calls(self):
        path=self.control.with_suffix('.jsonl')
        return [json.loads(s) for s in path.read_text().splitlines()] if path.exists() else []

    def cli(self, *args):
        output=io.StringIO()
        with patch('novel_engine.narration_codex.CodexNarrator', LocalNarrator), patch('novel_engine.planner.CodexPlanner',LocalPlanner), redirect_stdout(output):
            code=main(['--campaign',str(self.c.path),'--codex','local','--engine','codex','--json',*args])
        self.assertEqual(code,0)
        return json.loads(output.getvalue())

    def test_sixteen_turns_transport_agenda_memory_planning_and_display(self):
        for i in range(16):
            action='I work until the evening.' if i==12 else 'I carry on with my step.'
            result=self.cli('--text',action)
            self.assertEqual(result['status'],'recorded',result)
            self.assertEqual(result['narration'], self.c.resume()['session']['scene'])
            self.assertEqual(self.c.state()['state_version'],i+1)
            if i==1:
                self.assertEqual(result['world_minute_after'],3)
                with self.c.transaction() as con:
                    entry=Agenda(con).get('chronicle:test_visit')
                    self.assertEqual(entry['status'],'finished')
                    self.assertEqual(entry['audience'],['narrator'])
                    self.assertEqual(Agenda(con).packet(self.c._state(con))['interrupted_goal']['status'],'suspended')
        self.assertEqual(sum(c['role']=='narrator' for c in self.calls()),16)
        self.assertEqual(sum(c['role']=='planner' for c in self.calls()),1)
        self.assertTrue(self.c.memories('step')['proofs'])
        self.assertTrue(self.c.world_summaries()['summaries'])
        count=len(self.calls())
        for args in (('--json',),('--agenda',),('--summaries',),('--memories','step'),('--resume',)):
            self.cli(*args)
        self.assertEqual(len(self.calls()),count)

    def test_failures_memory_recovery_and_explicit_retry(self):
        self.cli('--text','I begin.')
        self.control.write_text('outage')
        before=self.c.state()
        with self.assertRaises(ModelUnavailable): self.cli('--text','I carry on.')
        count=len(self.calls()); pending=self.c.resume()['session']['pending_action']
        with self.assertRaises(ModelUnavailable): self.cli('--resume')
        self.assertEqual(len(self.calls()),count)
        self.assertEqual(self.c.state(),before)
        result=self.cli('--resume','--retry')
        self.assertEqual(result['request_id'],pending['request_id'])
        self.control.write_text('memory')
        before=self.c.state()
        result=self.cli('--text','I remember this step.')
        self.assertEqual(result['status'],'memory_required')
        self.assertEqual(self.c.state(),before)
        result=self.cli('--resume')
        self.assertEqual(result['status'],'recorded')
        self.control.write_text('invalid_reference')
        before=self.c.state()
        with self.assertRaises(ModelUnavailable):self.cli('--text','I finish.')
        self.assertEqual(self.c.state(),before)
        self.assertEqual(self.cli('--resume','--retry')['status'],'recorded')

    def test_crash_received_prepared_and_after_commit_never_regenerates(self):
        loop=self.loop()
        with patch.object(loop,'_proposal',side_effect=OSError('Crash after receipt')):
            with self.assertRaises(OSError):loop.run_turn('I begin.')
        count=len(self.calls())
        self.assertEqual(self.loop().run_turn(resume=True)['status'],'recorded')
        self.assertEqual(len(self.calls()),count)
        with patch.object(self.c,'apply',side_effect=OSError('Crash before transaction')):
            with self.assertRaises(OSError):self.loop().run_turn('I carry on.')
        count=len(self.calls())
        self.assertEqual(self.loop().run_turn(resume=True)['status'],'recorded')
        self.assertEqual(len(self.calls()),count)
        original=self.c.apply
        def commit_then_crash(*a,**kw):
            original(*a,**kw)
            raise OSError('Crash after commit')
        with patch.object(self.c,'apply',side_effect=commit_then_crash):
            with self.assertRaises(OSError):self.loop().run_turn('I finish.')
        count=len(self.calls());before=self.c.state()
        result=self.loop().run_turn(resume=True)
        self.assertEqual(result['narration'],self.c.resume()['session']['scene'])
        self.assertEqual(self.c.state(),before)
        self.assertEqual(len(self.calls()),count)

    def test_schema_preflight_context_growth_and_missing_type(self):
        for count in (0,1,64,200,1500):
            refs=['exchange:'+str(n) for n in range(count)]
            for schema in (response_schema(references=refs,version='1.2'),planner_schema({'exchanges_to_summarize':[{'reference':r} for r in refs],'allowed_references':refs,'chronicle':{}})):
                wire,digest=compile_schema(schema)
                self.assertEqual(len(digest),64)
                self.assertEqual(wire['type'],'object')
        bad=response_schema();del bad['properties']['narrative_schema_version']['type']
        with self.assertRaisesRegex(ValueError,'explicit JSON type'):compile_schema(bad)
        adapter=LocalNarrator(executable='local')
        with patch.object(adapter,'schema_for_prompt',return_value=bad),patch('novel_engine.narration_codex.subprocess.Popen') as process:
            with self.assertRaises(ValueError):adapter.generate('Test')
            process.assert_not_called()
            self.assertEqual(adapter.last_metrics['model_calls'],0)

    def test_one_event_contract_for_proposal_result_and_transport(self):
        event=load_schema('proposal')['properties']['events']['items']
        self.assertEqual(event,load_schema('result')['properties']['applied_events']['items'])
        self.assertEqual(event['properties']['changes']['minItems'],0)
        with self.assertRaises(ValueError):load_schema('../proposition')

    def test_interruption_can_complete_goal_but_cannot_cross_its_time(self):
        self.cli('--text','I begin.')
        prepared=self.c.prepare_turn('I wait for the messenger.',narrative_memory=True)
        loop=self.loop();response=loop.narrator.generate(loop.build_prompt(prepared))
        response['continuation'].update(status='finished',event_index=None,remaining='The messenger has arrived.')
        overshoot=deepcopy(response)
        later=deepcopy(response['events'][0]);later.update(world_minute=4,causes=['current_action'])
        overshoot['events'].append(later)
        with self.assertRaisesRegex(ValueError,'goes past an interruption'):loop._proposal(prepared,overshoot)
        proposal=loop._proposal(prepared,response)
        result=self.c.apply(proposal,memory_updates=loop._memory_updates,agenda_changes=loop._agenda_changes)
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(result['world_minute_after'],3)

    def test_stderr_warning_does_not_hide_structured_multiline_error(self):
        stderr='Exceeded skills context budget.\n'+json.dumps({'type':'error','error':{
            'code':'invalid_json_schema','message':"schema must have a 'type' key."},'status':400},indent=2)
        error=collect_failure('',stderr,1)
        self.assertEqual(error['category'],'schema')
        self.assertEqual(error['http_status'],400)
        self.assertNotIn('skills',failure_summary(error))

    def test_observed_encounter_and_private_followup_require_distinct_events(self):
        entry={'entry_id':'private_followup_test','expected_revision':-1,'title':'Private follow-up',
               'actor':'craftsman','location':'town_gate','kind':'appointment','due_minute':2,
               'conditions':[],'references':[],'audience':['narrator'],'interrupt':False,
               'status':'planned','reason':'Isolated test.','outcome_event_index':None}
        with self.c.transaction() as con:Agenda(con)._put(entry,'test',0)
        prepared=self.c.prepare_turn('I meet the craftsman.',narrative_memory=True)
        loop=NarrativeLoop(self.c,None)
        packet=json.loads(loop.build_prompt(prepared).rsplit('\nCURRENT TURN\n',1)[1])
        public={'world_minute':2,'description':"The craftsman answers the player's request.",
                'causes':['current_action','agenda:private_followup_test'], 'conditions':[],
                'changes':[],'asset_operations':[],'audience':['narrator','player']}
        response={'narrative_schema_version':'1.2','narration':'The craftsman answers you.',
                  'events':[public],'blocking_questions':[],'memory_request':None,'memory_updates':[],
                  'agenda_updates':[{'entry_id':'private_followup_test','status':'finished',
                      'reason':'Follow-up handled.','references':['agenda:private_followup_test'],
                      'outcome_event_index':0,'due_minute':None,'creation':None}],
                  'continuation':None,'roll_checks':[]}
        before=self.c.state()
        with self.assertRaisesRegex(ValueError,'unauthorized audience.*private_followup_test'):
            loop._proposal(prepared,deepcopy(response),packet)
        self.assertEqual(self.c.state(),before)
        response['events'][0]['causes']=['current_action']
        private=deepcopy(public)
        private.update(description='Private follow-up closed after the observed fact.',
                       causes=['agenda:private_followup_test','current_event:0'],audience=['narrator'])
        response['events'].append(private)
        response['agenda_updates'][0]['outcome_event_index']=1
        proposal=loop._proposal(prepared,response,packet)
        result=self.c.apply(proposal,agenda_changes=loop._agenda_changes,memory_updates=loop._memory_updates)
        self.assertEqual(result['status'],'recorded')
        with self.c.transaction() as con:
            self.assertEqual(Agenda(con).get('private_followup_test')['audience'],['narrator'])
        self.assertEqual(result['narration'],'The craftsman answers you.')

    def test_chronicle_sign_may_be_reported_by_a_named_npc(self):
        entry={'entry_id':'chronicle:test_sign','expected_revision':-1,'title':'Signe',
               'actor':'guard','location':'town_gate','kind':'risk','due_minute':2,
               'conditions':[],'references':[],'audience':['narrator'],'interrupt':False,
               'status':'planned','reason':'Isolated test.','outcome_event_index':None}
        with self.c.transaction() as con:Agenda(con)._put(entry,'test',0)
        prepared=self.c.prepare_turn('I listen to the craftsman.',narrative_memory=True)
        loop=NarrativeLoop(self.c,None)
        packet=json.loads(loop.build_prompt(prepared).rsplit('\nCURRENT TURN\n',1)[1])
        rumor={'world_minute':2,'description':'The craftsman reports a rumor.',
               'causes':['current_action','agenda:chronicle:test_sign'],'conditions':[],
               'changes':[],'asset_operations':[],'audience':['narrator','player','npc:craftsman']}
        response={'narrative_schema_version':'1.2','narration':'The craftsman speaks.',
                  'events':[rumor],'blocking_questions':[],'memory_request':None,'memory_updates':[],
                  'agenda_updates':[{'entry_id':'chronicle:test_sign','status':'finished',
                      'reason':'The sign reaches Mira.','references':['agenda:chronicle:test_sign'],
                      'outcome_event_index':0,'due_minute':None,'creation':None}],
                  'continuation':None,'roll_checks':[]}
        loop._proposal(prepared,response,packet)
        response['events'][0]['audience']=['narrator','player','npc:craftsman','faction:guard']
        with self.assertRaisesRegex(ValueError,'unauthorized audience'):
            loop._proposal(prepared,deepcopy(response),packet)


if __name__=='__main__':unittest.main()
