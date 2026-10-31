"""Reliability of the loop without consuming remote calls."""
from copy import deepcopy
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from novel_engine.adapters import ModelUnavailable
from novel_engine.campaign import Campaign
from novel_engine.demo import initial_state,configuration,action
from novel_engine.narrative import NarrativeLoop,narrative_lock,select_facts
from novel_engine.narration_codex import CodexNarrator,find_codex,response_schema
from novel_engine.scripts.verify_game_kit import validate,check_schema
from novel_engine.scripts.play_narrative import main


class FakeNarrator:
    def __init__(self,response=None):
        self.response=response or {'narration':('"Hello."\n\n'
                                                   'The guard looks at you.'),
                                  'events':[],'blocking_questions':[]}
        self.calls=0
        self.last_metrics={}

    def generate(self,prompt):
        self.calls+=1
        return deepcopy(self.response)


class NarrativeTests(unittest.TestCase):
    def setUp(self):
        base=ROOT/'.tmp-tests';base.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=base)
        self.c=Campaign(Path(self.temp.name)/'novel_engine.sqlite').create(initial_state(),configuration())
        self.c.open_scene('The guard waits at the door.')
        self.narrator=FakeNarrator()
        self.loop=NarrativeLoop(self.c,self.narrator)

    def tearDown(self):
        self.temp.cleanup()

    def test_single_call_commits_before_return_and_preserves_exact_text(self):
        result=self.loop.run_turn('I say hello.')
        self.assertEqual(self.narrator.calls,1)
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(self.c.resume()['session']['scene'],result['narration'])
        self.assertEqual(result['narration'],self.narrator.response['narration'])
        self.assertEqual(self.c.resume()['exchange_count'],3)
        self.assertEqual(result['world_minute_after'],0)
        self.assertNotIn('applied_events',result)

    def test_startup_does_not_call_model_or_resume_pending_automatically(self):
        self.c.begin_action('I say hello.')
        self.assertEqual(self.loop.run_turn()['status'],'resume')
        with self.assertRaises(ModelUnavailable):self.loop.run_turn('I say hello.')
        self.assertEqual(self.narrator.calls,0)
        self.assertEqual(self.loop.run_turn(resume=True)['status'],'recorded')

    def test_failure_preserves_action_and_requires_explicit_retry(self):
        with patch.object(self.narrator,'generate',side_effect=ModelUnavailable('Disconnection')):
            with self.assertRaisesRegex(ModelUnavailable,'Generation interrupted or invalid.*Disconnection'):
                self.loop.run_turn('I speak.')
        pending=self.c.resume()['session']['pending_action']
        with self.assertRaises(ModelUnavailable):self.loop.run_turn(resume=True)
        self.assertEqual(self.narrator.calls,0)
        result=self.loop.run_turn(resume=True,retry=True)
        self.assertEqual(result['request_id'],pending['request_id'])
        self.assertEqual(self.narrator.calls,1)
        self.assertEqual(self.c.resume()['exchange_count'],3)

    def test_crash_after_generation_reuses_saved_proposal_without_call(self):
        apply=self.c.apply
        with patch.object(self.c,'apply',side_effect=OSError('Stop before application')):
            with self.assertRaises(OSError):self.loop.run_turn('I speak.')
        self.assertEqual(self.narrator.calls,1)
        result=self.loop.run_turn(resume=True)
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(self.narrator.calls,1)
        self.assertTrue(self.loop.last_metrics['reused_answer'])
        self.assertEqual(self.loop.last_metrics['model_calls'],0)

    def test_obsolete_generation_is_rejected_and_not_displayable(self):
        generate=self.narrator.generate
        def intervening(prompt):
            p=action(self.c.state(),'wait')
            # Immediate event to test a real concurrent write.
            p['events'][0]['world_minute']=0
            self.c.apply(p,background=True)
            return generate(prompt)
        with patch.object(self.narrator,'generate',side_effect=intervening):
            result=self.loop.run_turn('I speak.')
        self.assertEqual(result['status'],'rejected')
        self.assertEqual(result['narration'],'')
        self.assertEqual(self.c.resume()['session']['scene'],'The guard waits at the door.')
        with self.assertRaises(ModelUnavailable):self.loop.run_turn(resume=True)
        retried=self.loop.run_turn(reevaluate=True)
        self.assertEqual(retried['status'],'recorded')
        self.assertNotEqual(retried['request_id'],result['request_id'])
        self.assertEqual(self.c.resume()['exchange_count'],3)

    def test_invalid_json_value_and_untrusted_causes_preserve_state(self):
        event={'world_minute':0,'description':'Movement.',
               'causes':['demo:initial_configuration'],'conditions':[],
               'changes':[{'operation':'set','path':'/positions/player','value_json':'{invalid'}],
               'audience':['narrator','player']}
        self.narrator.response['events']=[event]
        before=self.c.state()
        with self.assertRaises(ModelUnavailable):self.loop.run_turn('I move.')
        self.assertEqual(self.c.state(),before)
        self.narrator.response['events'][0]['changes'][0]['value_json']='"route"'
        self.narrator.response['events'][0]['causes']=['canon:invented_fact']
        with self.assertRaises(ModelUnavailable):self.loop.run_turn(resume=True,retry=True)
        self.assertEqual(self.c.state(),before)

    def test_plain_text_value_json_is_accepted_as_string_only_outside_json_syntax(self):
        self.narrator.response['events']=[{'world_minute':1,'description':'Movement.',
            'causes':['demo:initial_configuration'],'conditions':[],
            'changes':[{'operation':'set','path':'/positions/player','value_json':'north road'}],
            'audience':['narrator','player']}]
        result=self.loop.run_turn('I leave.')
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(self.c.state()['positions']['player'],'north road')
        self.assertEqual(self.loop.last_metrics['value_json_raw_text'],1)

    def test_structured_changes_and_program_generated_ids(self):
        self.narrator.response['events']=[{'world_minute':2,'description':'Movement.',
            'causes':['demo:initial_configuration'],
            'conditions':[{'path':'/positions/player','equals_json':'"town_gate"'}],
            'changes':[{'operation':'set','path':'/positions/player','value_json':'"route"'}],
            'audience':['narrator','player']}]
        self.loop.run_turn('I leave.')
        self.assertEqual(self.c.state()['positions']['player'],'route')
        self.assertEqual(self.c.state()['world_minute'],2)
        self.assertEqual(len(self.c.journal()),1)

    def test_rejected_missing_field_keeps_action_and_reevaluation_receives_reason(self):
        self.narrator.response['events']=[{'world_minute':0,'description':'Update.',
            'causes':['demo:initial_configuration'],
            'conditions':[{'path':'/commitments/accord status','equals_json':'"open"'}],
            'changes':[{'operation':'set','path':'/positions/player','value_json':'"route"'}],
            'audience':['narrator','player']}]
        before=self.c.state()
        rejected=self.loop.run_turn('I work.')
        self.assertEqual(rejected['status'],'rejected')
        self.assertEqual(self.c.state(),before)
        with self.assertRaisesRegex(ModelUnavailable,'Action rejected: Condition not met'):
            self.loop.run_turn(resume=True,retry=True)
        self.assertEqual(self.narrator.calls,1)
        self.narrator.response['events']=[]
        generate=self.narrator.generate
        def corrected(prompt):
            packet=json.loads(prompt.split('\nCURRENT TURN\n')[1])
            self.assertEqual(packet['player_input'],'I work.')
            self.assertEqual(packet['reevaluation']['rejection_reason'],'Condition not met')
            return generate(prompt)
        with patch.object(self.narrator,'generate',side_effect=corrected):
            result=self.loop.run_turn(reevaluate=True)
        self.assertEqual(result['status'],'recorded')
        self.assertNotEqual(result['request_id'],rejected['request_id'])
        self.assertEqual(self.narrator.calls,2)
        self.assertEqual(self.c.resume()['exchange_count'],3)

    def test_question_does_not_change_world_and_is_saved(self):
        self.narrator.response={'narration':'','events':[],
                                'blocking_questions':['Which object do you mean?']}
        before=self.c.state()
        result=self.loop.run_turn('I take this object.')
        self.assertEqual(result['status'],'question')
        self.assertEqual(self.c.state(),before)
        self.assertEqual(self.c.resume()['session']['scene'],result['narration'])

    def test_empty_narration_is_refused_and_an_em_dash_is_kept(self):
        self.narrator.response['narration']=''
        with self.assertRaises(ModelUnavailable):self.loop.run_turn('I speak.')
        self.assertEqual(self.c.state()['state_version'],0)
        self.narrator.response['narration']='Texte'+chr(0x2014)+'suite'
        result=self.loop.run_turn(resume=True,retry=True)
        self.assertEqual(result['narration'],'Texte'+chr(0x2014)+'suite')

    def test_prompt_keeps_all_current_acquisitions_without_duplicate_scene(self):
        prepared=self.c.prepare_turn('I speak.')
        prompt=self.loop.build_prompt(prepared)
        packet=json.loads(prompt.split('\nCURRENT TURN\n')[1])
        self.assertNotIn('applied_event_ids',packet['current_state'])
        self.assertEqual(packet['current_state']['resources'],prepared['state']['resources'])
        self.assertEqual(packet['current_state']['powers'],prepared['state']['powers'])
        self.assertEqual(prompt.count('The guard waits at the door.'),1)
        self.assertEqual(packet['player_input'],'I speak.')

    def test_old_related_fact_is_recalled_alongside_recent_facts(self):
        facts=[{'id':str(i),'text':'Unrelated memory.','category':'fact','references':[str(i)],
                'audience':['narrator']} for i in range(20)]
        facts[0]['text']='The merchant awaits the promised letter.'
        selected=select_facts(facts,'I look for the merchant and his letter.')
        self.assertIn('0',[item['id'] for item in selected])
        self.assertTrue(all(str(i) in [item['id'] for item in selected] for i in range(14,20)))
        self.assertLessEqual(len(selected),10)

    def test_short_reply_recalls_full_journal_before_context_truncation(self):
        with self.c.transaction() as con:
            state=self.c._state(con)
            for i in range(130):
                event={'event_id':f'memory:{i}', 'world_minute':0,
                       'description':'Unrelated routine work.',
                       'causes':['demo:initial_configuration'], 'conditions':[], 'changes':[],
                       'audience':['narrator','player']}
                if i==0:
                    event['description']='The examiner signs the promotion report for mithril.'
                state['applied_event_ids'].append(event['event_id'])
                con.execute('INSERT INTO events VALUES (?,?,?)',
                            (event['event_id'],0,json.dumps(event,ensure_ascii=False)))
            con.execute('UPDATE campaign SET state=? WHERE id=1',(json.dumps(state),))
        self.narrator.response['narration']="The Guild checks the examiner's report for the promotion."
        self.loop.run_turn('I ask for news.')
        prepared=self.c.prepare_turn('Well, they have them.',narrative_memory=True)
        self.assertIn('memory:0',{item['id'] for item in prepared['context']['information']})
        packet=json.loads(self.loop.build_prompt(prepared).split('\nCURRENT TURN\n')[1])
        self.assertIn('memory:0',{item['id'] for item in packet['extra_facts']})
        self.assertLessEqual(packet['search_memory']['characters'],
                             packet['search_memory']['budget'])
        self.assertLessEqual(packet['search_memory']['candidates'],100)
        self.assertIn('memory:0',packet['allowed_references'])

    def test_recall_dialogue_without_events_and_ignore_archived_questions(self):
        self.narrator.response['narration']='Favorable evaluation: the file remains to be examined. Your rank remains copper.'
        self.loop.run_turn('What is the result of my evaluation?')
        saved_seq=self.c.resume()['recent_exchanges'][-1]['seq']
        self.narrator.response={'narration':'', 'events':[],
                               'blocking_questions':['Give back the missing file and evaluation.']}
        self.loop.run_turn('And my file?')
        question_seq=self.c.resume()['recent_exchanges'][-1]['seq']
        self.narrator.response={'narration':"Work at the carpenter's.", 'events':[], 'blocking_questions':[]}
        for i in range(8):
            self.loop.run_turn(f'I work, step {i}.')
        self.narrator.response['narration']='The Guild starts checking your evaluation file.'
        self.loop.run_turn('I return to the Guild.')
        prepared=self.c.prepare_turn('J’attends.',narrative_memory=True)
        packet=json.loads(self.loop.build_prompt(prepared).split('\nCURRENT TURN\n')[1])
        recalled={item['seq'] for item in packet['recalled_exchanges']}
        self.assertIn(saved_seq,recalled)
        self.assertNotIn(question_seq,recalled)
        self.assertEqual(self.c.journal(),[])
        self.assertIn('exchange:'+str(saved_seq),packet['allowed_references'])
        self.narrator.response['events']=[{'world_minute':0, 'description':'File consulted.',
            'causes':['exchange:'+str(saved_seq)], 'conditions':[],
            'changes':[{'operation':'set','path':'/intentions/player','value_json':'"Wait for the verification."'}],
            'audience':['narrator','player']}]
        proposal=self.loop._proposal(prepared,self.narrator.response)
        self.assertEqual(proposal['events'][0]['causes'],['exchange:'+str(saved_seq)])

    def test_only_one_narrative_writer_and_lock_released_after_failure(self):
        with narrative_lock(self.c):
            with self.assertRaises(ModelUnavailable):self.loop.run_turn('I speak.')
        self.assertEqual(self.narrator.calls,0)
        self.assertEqual(self.loop.run_turn('I speak.')['status'],'recorded')

    def test_cli_replay_is_local_and_missing_database_is_not_created(self):
        result=self.loop.run_turn('I speak.')
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            code=main(['--campaign',str(self.c.path),'--result',result['request_id'],'--json'])
        self.assertEqual(code,0)
        self.assertEqual(json.loads(output.getvalue())['narration'],result['narration'])
        missing=Path(self.temp.name)/'absent.sqlite'
        process=subprocess.run([sys.executable,str(ROOT/'novel_engine/scripts/play_narrative.py'),'--campaign',str(missing),
                                '--json'],capture_output=True,text=True,encoding='utf-8')
        self.assertNotEqual(process.returncode,0)
        self.assertFalse(missing.exists())

    def test_terminal_loop_saves_and_displays_then_quits(self):
        output=io.StringIO()
        with patch('novel_engine.narration_codex.CodexNarrator',return_value=self.narrator), \
             patch('builtins.input',side_effect=['I say hello.','/quit']), \
             patch('sys.stdin.isatty',return_value=False), \
             contextlib.redirect_stdout(output),contextlib.redirect_stderr(io.StringIO()):
            code=main(['--campaign',str(self.c.path),'--engine','codex'])
        self.assertEqual(code,0)
        self.assertEqual(self.narrator.calls,1)
        self.assertIn(self.c.resume()['session']['scene'],output.getvalue())
        self.assertEqual(self.c.resume()['exchange_count'],3)

    def test_engine_question_only_on_a_real_terminal(self):
        with patch('novel_engine.narration_codex.CodexNarrator',return_value=self.narrator), \
             patch('builtins.input',side_effect=['/quit']) as asked, \
             patch('sys.stdin.isatty',return_value=False), \
             contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            code=main(['--campaign',str(self.c.path),'--engine','codex'])
        self.assertEqual(code,0)
        self.assertEqual(asked.call_count,1)
        self.assertFalse((self.c.path.parent/'novel_engine.json').exists())

    def test_engine_choice_is_remembered_between_launches(self):
        preference=self.c.path.parent/'novel_engine.json'
        with patch('novel_engine.narration_claude.ClaudeNarrator',return_value=self.narrator) as narrator, \
             patch('novel_engine.narration_claude.ClaudePlanner'), \
             patch('builtins.input',side_effect=['claude','/quit']), \
             patch('sys.stdin.isatty',return_value=True), \
             contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            main(['--campaign',str(self.c.path)])
        self.assertTrue(narrator.called)
        self.assertEqual(json.loads(preference.read_text(encoding='utf-8')),{'engine':'claude'})
        error=io.StringIO()
        # The saved choice only counts while the engine is usable: a machine without the Claude CLI would offer manual.
        with patch('novel_engine.engines.available',return_value=['claude','manual']),              patch('novel_engine.narration_claude.ClaudeNarrator',return_value=self.narrator) as narrator, \
             patch('novel_engine.narration_claude.ClaudePlanner'), \
             patch('builtins.input',side_effect=['','/quit']), \
             patch('sys.stdin.isatty',return_value=True), \
             contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(error):
            main(['--campaign',str(self.c.path)])
        self.assertIn('[claude]',error.getvalue())
        self.assertTrue(narrator.called)

    def test_codex_configuration_forces_standard_and_disables_fast(self):
        adapter=CodexNarrator(executable='codex.exe')
        command=adapter.command(Path(self.temp.name)/'schema.json',self.temp.name)
        self.assertIn('model_reasoning_effort="low"',command)
        self.assertIn('service_tier="standard"',command)
        self.assertIn('--ignore-user-config',command)
        self.assertIn('shell_tool',command)
        self.assertIn('multi_agent',command)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox',command)
        self.assertNotIn('--enable',command)
        self.assertNotIn('service_tier="fast"',command)
        for feature in ('fast_mode','ultrafast_mode'):
            self.assertEqual(command[command.index(feature)-1],'--disable')
        with self.assertRaises(TypeError):CodexNarrator(fast=True,executable='codex.exe')

    def test_codex_found_without_path_uses_latest_local_binary(self):
        root=Path(self.temp.name)/'OpenAI'/'Codex'/'bin'
        recent=root/'version_recent'/'codex.exe'
        recent.parent.mkdir(parents=True)
        old=root/'codex.exe'
        old.write_bytes(b'old')
        recent.write_bytes(b'recent')
        os.utime(old,(1000,1000))
        os.utime(recent,(2000,2000))
        with patch('novel_engine.narration_codex.shutil.which',return_value=None), \
             patch('novel_engine.narration_codex.Path.home',return_value=Path(self.temp.name)), \
             patch.dict(os.environ,{'LOCALAPPDATA':self.temp.name}):
            self.assertEqual(CodexNarrator().executable,str(recent))
            self.assertEqual(CodexNarrator(executable='choice.exe').executable,'choice.exe')

    def test_codex_lookup_preserves_path_priority_and_missing_error(self):
        with patch('novel_engine.narration_codex.shutil.which',return_value='in_path.exe'):
            self.assertEqual(find_codex(),'in_path.exe')
        with patch('novel_engine.narration_codex.shutil.which',return_value=None), \
             patch('novel_engine.narration_codex.Path.home',return_value=Path(self.temp.name)), \
             patch.dict(os.environ,{'LOCALAPPDATA':self.temp.name}):
            self.assertIsNone(find_codex())
            with self.assertRaises(ModelUnavailable):CodexNarrator().command('schema','file')

    def test_codex_found_when_windows_redirects_localappdata(self):
        profile=Path(self.temp.name)/'profile'
        executable=profile/'AppData'/'Local'/'OpenAI'/'Codex'/'bin'/'version'/'codex.exe'
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b'recent')
        with patch('novel_engine.narration_codex.shutil.which',return_value=None), \
             patch('novel_engine.narration_codex.Path.home',return_value=profile), \
             patch.dict(os.environ,{'LOCALAPPDATA':str(Path(self.temp.name)/'redirection')}):
            self.assertEqual(find_codex(),str(executable))

    def test_cli_refuses_fast_before_saving_or_calling_model(self):
        before=self.c.resume()
        with patch('novel_engine.narration_codex.CodexNarrator') as narrator, \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                main(['--campaign',str(self.c.path),'--text','I speak.','--fast','--engine','codex'])
        self.assertEqual(error.exception.code,2)
        narrator.assert_not_called()
        self.assertEqual(self.c.resume(),before)

    def test_codex_decodes_warning_and_final_message_without_exposing_logs(self):
        self.narrator.response.update(narrative_schema_version='1.2',memory_request=None, memory_updates=[],agenda_updates=[],continuation=None,roll_checks=[])
        events=[{'type':'item.completed','item':{'type':'error','message':'Avertissement local'}},
                {'type':'item.completed','item':{'type':'agent_message','text':json.dumps(self.narrator.response)}},
                {'type':'turn.completed','usage':{'input_tokens':100,'output_tokens':80}}]
        process=unittest.mock.Mock()
        process.returncode=0
        process.communicate.return_value=('\n'.join(json.dumps(e) for e in events).encode(),b'log private')
        adapter=CodexNarrator(executable='codex.exe')
        with patch('novel_engine.narration_codex.subprocess.Popen',return_value=process):
            response=adapter.generate('Private context')
        self.assertEqual(response,self.narrator.response)
        self.assertEqual(adapter.last_metrics['cli_warning_count'],1)
        self.assertEqual(adapter.last_metrics['usage']['output_tokens'],80)
        events[0]['item']['type']='command_execution'
        process.communicate.return_value=('\n'.join(json.dumps(e) for e in events).encode(),b'')
        with patch('novel_engine.narration_codex.subprocess.Popen',return_value=process):
            with self.assertRaises(ModelUnavailable):adapter.generate('Private context')

    def test_constrained_schema_refuses_unknown_causes_and_invented_agenda_sources(self):
        schema=response_schema(references=['action:request','agenda:guild','exchange:205'])
        check_schema(schema)
        cause=schema['properties']['events']['items']['properties']['causes']['items']
        validate(cause,'current_action')
        validate(cause,'current_event:0')
        validate(cause,'agenda:guild')
        with self.assertRaises(ValueError):validate(cause,'agenda:new_file')
        with self.assertRaises(ValueError):validate(cause,'canon:missing_reference')
        sources=schema['properties']['agenda_updates']['items']['properties']['references']['items']
        with self.assertRaises(ValueError):validate(sources,'agenda:new_file')

    def test_action_alias_is_resolved_without_inventing_a_reference(self):
        self.narrator.response['events']=[{'world_minute':0,'description':'Exchange.',
            'causes':['current_action'],'conditions':[],
            'changes':[{'operation':'set','path':'/intentions/test','value_json':'"speak"'}],
            'audience':['narrator','player']}]
        result=self.loop.run_turn('I speak.')
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(self.c.journal()[0]['causes'],['action:'+result['request_id']])

    def test_failed_response_keeps_exact_bad_cause_and_pending_action(self):
        self.narrator.response['events']=[{'world_minute':0,'description':'Exchange.',
            'causes':['canon:missing_reference'],'conditions':[],
            'changes':[{'operation':'set','path':'/intentions/test','value_json':'"speak"'}],
            'audience':['narrator','player']}]
        before=self.c.state()
        with self.assertRaisesRegex(ModelUnavailable,'canon:missing_reference'):
            self.loop.run_turn('I speak.')
        pending=self.c.resume()['session']['pending_action']
        with self.c.transaction() as con:
            row=con.execute('SELECT * FROM narrative_diagnostics WHERE request_id=?',(pending['request_id'],)).fetchone()
            data=json.loads(row['payload'])
            self.assertEqual(data['response'],self.narrator.response)
            self.assertEqual(data['missing_causes'],[{'event_index':0,'reference':'canon:missing_reference'}])
            self.assertIn('canon:missing_reference',row['error'])
        self.assertIn('canon:missing_reference',self.loop._previous_attempt(pending['request_id'])['reason'])
        self.assertEqual(self.c.state(),before)
        self.assertEqual(self.narrator.calls,1)

    def test_cli_schema_uses_supplied_references_and_retains_invalid_raw_response(self):
        prepared=self.c.prepare_turn('I speak.',narrative_memory=True)
        prompt=self.loop.build_prompt(prepared)
        bad=deepcopy(self.narrator.response)
        bad['events']=[{'world_minute':0,'description':'Exchange.',
            'causes':['canon:missing_reference'],'conditions':[],
            'changes':[{'operation':'set','path':'/intentions/test','value_json':'"speak"'}],
            'audience':['narrator','player']}]
        events=[{'type':'item.completed','item':{'type':'agent_message','text':json.dumps(bad)}}]
        process=unittest.mock.Mock();process.returncode=0
        process.communicate.return_value=('\n'.join(json.dumps(e) for e in events).encode(),b'')
        adapter=CodexNarrator(executable='codex.exe')
        observed=[]
        def launch(command,**kwargs):
            path=Path(command[command.index('--output-schema')+1])
            observed.append(json.loads(path.read_text(encoding='utf-8')))
            return process
        with patch('novel_engine.narration_codex.subprocess.Popen',side_effect=launch):
            with self.assertRaises(ModelUnavailable):
                NarrativeLoop(self.c,adapter).run_turn(resume=True)
        cause=observed[0]['properties']['events']['items']['properties']['causes']['items']
        self.assertNotIn('canon:missing_reference',cause['enum'])
        self.assertIn('action:'+prepared['model_proposal']['request_id'],cause['enum'])
        self.assertEqual(json.loads(adapter.last_response),bad)
        with self.c.transaction() as con:
            data=json.loads(con.execute('SELECT payload FROM narrative_diagnostics').fetchone()[0])
            self.assertEqual(json.loads(data['response']),bad)

    def test_malformed_raw_output_is_retained_without_changing_world(self):
        adapter=CodexNarrator(executable='codex.exe')
        events=[{'type':'item.completed','item':{'type':'agent_message','text':'{invalid'}}]
        process=unittest.mock.Mock();process.returncode=0
        process.communicate.return_value=('\n'.join(json.dumps(e) for e in events).encode(),b'')
        before=self.c.state()
        with patch('novel_engine.narration_codex.subprocess.Popen',return_value=process):
            with self.assertRaises(ModelUnavailable):NarrativeLoop(self.c,adapter).run_turn('I speak.')
        with self.c.transaction() as con:
            data=json.loads(con.execute('SELECT payload FROM narrative_diagnostics').fetchone()[0])
            self.assertEqual(data['response'],'{invalid')
        self.assertEqual(self.c.state(),before)

    def test_diagnostics_escape_unicode_in_invalid_response(self):
        self.narrator.response['narration']='Texte'+chr(0x2014)+'suite'
        self.narrator.response['unknown_field']=True
        with self.assertRaises(ModelUnavailable):self.loop.run_turn('I speak.')
        with self.c.transaction() as con:
            text=con.execute('SELECT payload FROM narrative_diagnostics').fetchone()[0]
            self.assertTrue(text.isascii())
            self.assertEqual(json.loads(text)['response']['narration'],self.narrator.response['narration'])


if __name__=='__main__':
    unittest.main(verbosity=2)
