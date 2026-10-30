"""Tests of the local engine, of the races and of the separation of information."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from novel_engine.adapters import CommandAdapter, RecordedAdapter, DisabledAdapter, ModelUnavailable
from novel_engine.campaign import Campaign
from novel_engine.contracts import check
from novel_engine.demo import initial_state, configuration, event, proposal, action, task, handle_background
from novel_engine.background import Background
from novel_engine.memory import ContextBuilder, CanonRepository
from novel_engine.resume import resume_active, select_campaign


class EngineTests(unittest.TestCase):
    def setUp(self):
        base = ROOT/'.tmp-tests'
        base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        require_path = Path(self.temp.name).resolve()
        self.assertTrue(require_path.is_relative_to(base.resolve()))
        self.c = Campaign(require_path/'campaign.sqlite').create(initial_state(),configuration())
        self.workers = []

    def tearDown(self):
        for worker in self.workers:
            worker.close()
        self.temp.cleanup()

    def worker(self, handler=handle_background, **kwargs):
        worker = Background(self.c,handler,**kwargs)
        self.workers.append(worker)
        return worker

    def wait_status(self, worker, status):
        deadline = time.monotonic()+3
        while time.monotonic()<deadline:
            if any(row['status']==status for row in worker.status()):
                return
            time.sleep(0.01)
        self.fail(f'Status {status} missing: {worker.status()}')

    def test_atomic_failure_rolls_back_every_change(self):
        state=self.c.state()
        e=event(state,'Impossible purchase',[{'operation':'increment','path':'/resources/player_gold','value':-11},
                                        {'operation':'set','path':'/positions/player','value':'route'}])
        result=self.c.apply(proposal(state,'Purchase successful.',[e]))
        self.assertEqual(result['status'],'rejected')
        self.assertEqual(result['narration'],'')
        self.assertEqual(self.c.state(),state)
        self.assertEqual(self.c.journal('narrator'),[])

    def test_saved_result_replays_after_reopening_without_duplicate(self):
        p=action(self.c.state(),'leave')
        first=self.c.apply(p)
        reopened=Campaign(self.c.path)
        self.assertEqual(reopened.apply(p),first)
        self.assertEqual(reopened.result(p['request_id']),first)
        self.assertEqual(len(reopened.journal('narrator')),2)
        self.assertEqual(reopened.state()['state_version'],1)

    def test_request_id_cannot_change_content(self):
        p=action(self.c.state(),'leave')
        self.c.apply(p)
        p['narration']='Different text'
        with self.assertRaises(ValueError): self.c.apply(p)

    def test_event_cannot_be_spent_twice_with_another_request(self):
        state=self.c.state()
        e=event(state,'Expense',[{'operation':'increment','path':'/resources/player_gold','value':-3}])
        self.c.apply(proposal(state,events=[e]))
        result=self.c.apply(proposal(self.c.state(),events=[e]))
        self.assertEqual(result['status'],'rejected')
        self.assertEqual(self.c.state()['resources']['player_gold'],7)

    def test_concurrent_writers_accept_only_one_version(self):
        state=self.c.state()
        results=[]
        barrier=threading.Barrier(2)
        def run(destination):
            p=proposal(state,events=[event(state,'Movement',[{'operation':'set','path':'/positions/player','value':destination}])])
            barrier.wait()
            results.append(self.c.apply(p)['status'])
        threads=[threading.Thread(target=run,args=(x,)) for x in ('route','inn')]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertCountEqual(results,['recorded','rejected'])
        self.assertEqual(self.c.state()['state_version'],1)

    def test_future_background_event_does_not_advance_reading_clock(self):
        state=self.c.state()
        e=event(state,'Future project',[{'operation':'set','path':'/intentions/faction','value':'future'}],minute=10,audience=['narrator'])
        p=proposal(state,events=[e])
        result=self.c.apply(p,background=True)
        self.assertEqual(result['status'],'deferred')
        self.assertEqual(self.c.state(),state)
        self.assertIsNone(self.c.result(p['request_id']))

    def test_outdated_result_is_rejected(self):
        state=self.c.state()
        old=proposal(state,events=[event(state,'Ancien plan',[{'operation':'set','path':'/intentions/faction','value':'old'}])])
        self.c.apply(action(state,'leave'))
        self.assertEqual(self.c.apply(old)['status'],'rejected')
        self.assertEqual(self.c.state()['intentions']['faction'],'observe_arrivals')

    def test_question_does_not_apply_or_advance(self):
        state=self.c.state()
        result=self.c.apply(action(state,'action unknown'))
        self.assertEqual(result['status'],'question')
        self.assertEqual(self.c.state(),state)



    def test_secret_event_and_effect_not_in_player_context(self):
        state=self.c.state()
        secret={'actor_id':'player','description':'Secret curse.','expires_at':None,'references':['demo:arrival_event'],'audience':['narrator']}
        e=event(state,'The traveler is secretly cursed.',[{'operation':'set','path':'/effects/curse','value':secret}],audience=['narrator'])
        self.c.apply(proposal(state,events=[e]))
        self.assertFalse(any('secret' in i['text'].casefold() for i in ContextBuilder(self.c).build('player')['information']))
        self.assertTrue(ContextBuilder(self.c).build('narrator')['information'])



    def test_wrong_canon_revision_fails(self):
        with self.assertRaises(ValueError):
            CanonRepository(self.c.state()['versions']['canon_snapshot'],77)

    def test_background_work_is_done_while_player_reads(self):
        worker=self.worker()
        worker.start(); worker.reading(max_calls=0)
        worker.enqueue(task(self.c.state()))
        self.wait_status(worker,'recorded')
        self.assertEqual(self.c.state()['intentions']['faction'],'watch_gates')
        self.assertEqual(self.c.state()['world_minute'],0)
        self.assertFalse(self.c.journal('player'))

    def test_player_cancels_running_work_before_it_can_write(self):
        begun=threading.Event()
        def slow(spec,state,cancel):
            begun.set(); cancel.wait(2)
            return handle_background(spec,state,cancel)
        worker=self.worker(slow)
        worker.start(); worker.reading(); worker.enqueue(task(self.c.state()))
        self.assertTrue(begun.wait(1))
        result=worker.submit_player(action(self.c.state(),'leave'))
        self.assertEqual(result['status'],'recorded')
        self.wait_status(worker,'cancelled')
        self.assertEqual(self.c.state()['intentions']['faction'],'observe_arrivals')

    def test_task_cannot_mutate_an_unauthorized_path(self):
        def bad(spec,state,cancel):
            return proposal(state,events=[event(state,'Effet forbidden',[{'operation':'set','path':'/positions/player','value':'prison'}],audience=['narrator'])],request_id=spec['task_id'])
        worker=self.worker(bad); worker.start();worker.reading();worker.enqueue(task(self.c.state()))
        self.wait_status(worker,'error')
        self.assertEqual(self.c.state()['positions']['player'],'town_gate')

    def test_conditional_preparation_never_applies_automatically(self):
        worker=self.worker(); worker.start();worker.reading()
        spec=task(self.c.state());spec['kind']='conditional'
        worker.enqueue(spec);self.wait_status(worker,'prepared')
        self.assertEqual(self.c.state()['state_version'],0)
        worker.submit_player(action(self.c.state(),'leave'));worker.settle_prepared()
        self.wait_status(worker,'rejected')

    def test_model_budget_zero_prevents_any_call(self):
        class Spy:
            calls=0
            def proposed(self,*args):self.calls+=1;raise RuntimeError()
        spy=Spy()
        worker=self.worker(adapter=spy,context_builder=lambda spec: {})
        worker.start();worker.reading(max_calls=0)
        spec=task(self.c.state());spec['handler']='model';spec['budget']['max_model_calls']=1
        worker.enqueue(spec);self.wait_status(worker,'rejected')
        self.assertEqual(spy.calls,0)

    def test_queue_is_bounded_and_duplicate_task_uses_no_slot(self):
        worker=self.worker();worker.reading(max_tasks=1)
        spec=task(self.c.state());worker.enqueue(spec);worker.enqueue(spec)
        with self.assertRaises(ValueError):worker.enqueue(task(self.c.state()))
        self.assertEqual(len(worker.status()),1)

    def test_interrupted_process_marks_unfinished_work_for_review(self):
        worker=self.worker();worker.reading();worker.enqueue(task(self.c.state()))
        Campaign(self.c.path).recover_tasks()
        self.assertEqual(worker.status()[0]['status'],'cancelled')
        self.assertEqual(self.c.state()['state_version'],0)

    def test_effect_expiry_is_recorded_and_filtered(self):
        state=self.c.state()
        effect={'actor_id':'player','description':'Fatigue temporary.','expires_at':2,'references':['demo:arrival_event'],'audience':['narrator','player']}
        self.c.apply(proposal(state,events=[event(state,'Fatigue present.',[{'operation':'set','path':'/effects/fatigue','value':effect}])]))
        self.c.apply(action(self.c.state(),'wait 2'))
        worker=self.worker();worker.start();worker.reading();worker.enqueue(task(self.c.state(),'expiration'))
        self.wait_status(worker,'recorded')
        self.assertNotIn('fatigue',self.c.state()['effects'])
        self.assertTrue(any('comes to an end' in e['description'] for e in self.c.journal('player')))

    def test_disabled_adapter_has_no_fallback_network(self):
        with self.assertRaises(ModelUnavailable):DisabledAdapter().proposed({}, {})

    def test_external_adapter_checks_structured_response(self):
        adapter=CommandAdapter([sys.executable,'-c','print("not JSON")'])
        with self.assertRaises(ModelUnavailable):adapter.proposed({},task(self.c.state()))

    def test_prohibited_punctuation_and_boolean_resource_fail(self):
        state=self.c.state();state['intentions']['faction']=chr(0x2014)
        with self.assertRaises(ValueError):check('state',state)
        state=initial_state();state['resources']['player_gold']=True
        with self.assertRaises(ValueError):check('state',state)

    def test_inventory_powers_and_relations_persist(self):
        state=self.c.state();audience=['narrator','player'];refs=['demo:arrival_event']
        values={
            '/inventory/letter':{'owner_id':'player','quantity':1,'equipped':False,'description':'Sealed letter.','references':refs,'audience':audience},
            '/powers/reader':{'actor_id':'player','description':'Reader ordinary.','limits':'No magical power attributed.','references':refs,'audience':audience},
            '/relations/merchant':{'from_id':'player','to_id':'merchant','attitude':'neutral','references':refs,'audience':audience}}
        changes=[{'operation':'set','path':path,'value':value} for path,value in values.items()]
        self.assertEqual(self.c.apply(proposal(state,events=[event(state,'Demo state.',changes)]))['status'],'recorded')
        reopened=Campaign(self.c.path).state()
        self.assertEqual(reopened['inventory']['letter']['quantity'],1)
        self.assertEqual(len(ContextBuilder(self.c).build('player')['information']),5)

    def test_current_resources_and_intentions_are_scoped_to_their_owner(self):
        player=ContextBuilder(self.c).build('player')['information']
        faction=ContextBuilder(self.c).build('npc:faction')['information']
        self.assertTrue(any(i['id']=='resource:player_gold' for i in player))
        self.assertFalse(any(i['id']=='intention:faction' for i in player))
        self.assertTrue(any(i['id']=='intention:faction' for i in faction))
        self.assertFalse(any(i['id']=='resource:player_gold' for i in faction))

    def test_sources_are_protected_from_campaign_writes(self):
        for path in ('canon/canon.sqlite','corpus/library.sqlite','sources/test.sqlite'):
            with self.assertRaises(ValueError):Campaign(ROOT/path)

    def test_equivalent_json_key_order_has_same_idempotency_digest(self):
        p=action(self.c.state(),'leave')
        first=self.c.apply(p)
        reordered=dict(reversed(list(p.items())))
        self.assertEqual(self.c.apply(reordered),first)

    def test_prepared_future_event_is_discarded_after_player_changes_state(self):
        def future(spec,state,cancel):
            e=event(state,'Surveillance future.',[{'operation':'set','path':'/intentions/faction','value':'watch_gates'}],minute=10,audience=['narrator'])
            return proposal(state,events=[e],request_id=spec['task_id'])
        worker=self.worker(future);worker.start();worker.reading();worker.enqueue(task(self.c.state()))
        self.wait_status(worker,'deferred')
        worker.submit_player(action(self.c.state(),'wait 10'));worker.settle_prepared()
        self.wait_status(worker,'rejected')
        self.assertEqual(self.c.state()['intentions']['faction'],'observe_arrivals')

    def test_recorded_adapter_and_global_model_budget(self):
        state=self.c.state();spec=task(state)
        spec['handler']='model';spec['budget']['max_model_calls']=1
        prepared=handle_background(spec,state,threading.Event())
        adapter=RecordedAdapter({spec['task_id']:prepared})
        worker=self.worker(adapter=adapter,context_builder=lambda t:ContextBuilder(self.c).build('npc:faction'))
        worker.start();worker.reading(max_calls=1);worker.enqueue(spec)
        self.wait_status(worker,'recorded')
        # New task, same reading period: the first call consumed the budget.
        second=task(self.c.state(),'expiration')
        if second is None:
            second=deepcopy(spec);second['task_id']='second';second['expected_state_version']=self.c.state()['state_version']
        worker.enqueue(second);self.wait_status(worker,'rejected')


    def test_invalid_task_audience_is_rejected(self):
        def leaking(spec,state,cancel):
            e=event(state,'Secret revealed.',[{'operation':'set','path':'/intentions/faction','value':'watch_gates'}])
            return proposal(state,events=[e],request_id=spec['task_id'])
        worker=self.worker(leaking);worker.start();worker.reading();worker.enqueue(task(self.c.state()))
        self.wait_status(worker,'error')
        self.assertFalse(self.c.journal('player'))

    def test_real_terminal_session_works_during_reading_and_resumes(self):
        process=subprocess.Popen([sys.executable,str(ROOT/'novel_engine/scripts/play_demo.py'),'--campaign',str(self.c.path)],
                                 stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                 text=True,encoding='utf-8')
        def wait_intention(expected):
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                if self.c.state()['intentions']['faction']==expected:return
                if process.poll() is not None:break
                time.sleep(0.02)
            self.fail('The interactive session does not progress as expected')
        try:
            wait_intention('watch_gates')
            process.stdin.write('leave\n');process.stdin.flush()
            wait_intention('have_follow_route')
            output,error=process.communicate('memories\nquitter\n',timeout=5)
            self.assertEqual(process.returncode,0,error)
            self.assertIn('take the road',output)
            self.assertNotIn('lookout',output)
            self.assertNotIn('surveillance',output)
            self.assertEqual(Campaign(self.c.path).state()['positions']['player'],'route')
            self.assertTrue(self.c.path.with_name('RESUME.md').is_file())
        finally:
            if process.poll() is None:
                process.kill();process.communicate(timeout=5)

    def test_external_adapter_accepts_valid_json_without_shell(self):
        spec=task(self.c.state())
        response=handle_background(spec,self.c.state(),threading.Event())
        local=Path(self.temp.name)/'response.json'
        local.write_text(json.dumps(response,ensure_ascii=False),encoding='utf-8')
        adapter=CommandAdapter([sys.executable,'-c','import pathlib,sys;print(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))',str(local)])
        self.assertEqual(adapter.proposed({},spec),response)

    def test_initial_state_can_be_imported_through_cli(self):
        local=Path(self.temp.name)/'initial.json'
        state=initial_state('autre_demo')
        local.write_text(json.dumps(state,ensure_ascii=False),encoding='utf-8')
        database=Path(self.temp.name)/'import.sqlite'
        result=subprocess.run([sys.executable,str(ROOT/'novel_engine/scripts/campaign_cli.py'),'--campaign',str(database),
                               'init','--initial-state',str(local)],capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(Campaign(database).state()['campaign_id'],'autre_demo')

    def test_resume_keeps_exact_scene_input_and_result_after_reopening(self):
        opening='The guard asks you: "Your name?"'
        self.c.open_scene(opening)
        pending=self.c.begin_action('leave')
        interrupted=Campaign(self.c.path).resume()
        self.assertEqual(interrupted['session']['scene'],opening)
        self.assertEqual(interrupted['session']['pending_action'],pending)
        self.assertEqual(interrupted['session']['phase'],'action_to_resolve')
        p=action(self.c.state(),'leave');p['request_id']=pending['request_id']
        result=self.c.apply(p)
        restored=Campaign(self.c.path).resume()
        self.assertEqual(restored['session']['scene'],result['narration'])
        self.assertIsNone(restored['session']['pending_action'])
        process=subprocess.run([sys.executable,str(ROOT/'novel_engine/scripts/campaign_cli.py'),
                                '--campaign',str(self.c.path),'session'],
                               capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(process.returncode,0,process.stderr)
        self.assertEqual(json.loads(process.stdout),restored)
        self.assertEqual([r['content'] for r in restored['recent_exchanges']],
                         [opening,'leave',result['narration']])
        self.assertEqual(Campaign(self.c.path).apply(p),result)
        self.assertEqual(Campaign(self.c.path).resume(),restored)

    def test_background_updates_do_not_replace_visible_resume(self):
        self.c.open_scene('You read the signpost.')
        before=self.c.resume()['session']
        state=self.c.state();spec=task(state)
        self.c.apply(handle_background(spec,state,threading.Event()),background=True)
        restored=Campaign(self.c.path).resume()
        self.assertEqual(restored['session'],before)
        self.assertNotIn('faction',json.dumps(restored,ensure_ascii=False))
        self.assertEqual(restored['state_version'],1)

    def test_pending_action_blocks_other_inputs_and_wrong_result_atomically(self):
        self.c.open_scene('Initial scene.')
        pending=self.c.begin_action('leave')
        self.assertEqual(self.c.begin_action('leave'),pending)
        with self.assertRaises(ValueError):self.c.begin_action('wait')
        before=self.c.state()
        p=action(before,'leave')
        with self.assertRaises(ValueError):self.c.apply(p)
        self.assertEqual(self.c.state(),before)
        self.assertIsNone(self.c.result(p['request_id']))
        self.assertEqual(self.c.resume()['exchange_count'],2)

    def test_stale_result_preserves_action_for_reevaluation(self):
        self.c.open_scene('At the gates.')
        pending=self.c.begin_action('leave')
        state=self.c.state()
        p=action(state,'leave');p['request_id']=pending['request_id']
        spec=task(state)
        self.c.apply(handle_background(spec,state,threading.Event()),background=True)
        self.assertEqual(self.c.apply(p)['status'],'rejected')
        self.assertEqual(self.c.resume()['session']['pending_action'],pending)
        self.assertEqual(self.c.resume()['session']['scene'],'At the gates.')
        retry=self.c.reevaluate_action()
        self.assertNotEqual(retry['request_id'],pending['request_id'])
        self.assertEqual(retry['text'],pending['text'])
        p2=action(self.c.state(),retry['text']);p2['request_id']=retry['request_id']
        self.assertEqual(self.c.apply(p2)['status'],'recorded')
        self.assertEqual(self.c.apply(p)['status'],'rejected')
        self.assertEqual(self.c.resume()['exchange_count'],3)

    def test_resume_migration_does_not_reinitialize_legacy_state(self):
        self.c.apply(action(self.c.state(),'leave'))
        before=self.c.state()
        with self.c.transaction() as con:
            con.execute('DROP TABLE session');con.execute('DROP TABLE exchanges')
        saved=Campaign(self.c.path).resume()
        self.assertEqual(saved['state_version'],before['state_version'])
        self.assertEqual(Campaign(self.c.path).state(),before)
        self.assertEqual(saved['session']['phase'],'no_scene')

    def test_question_and_initial_scene_are_persistent_without_time_advance(self):
        self.c.open_scene('Opening recorded.')
        self.c.open_scene('Nouvelle opening forbidden.')
        pending=self.c.begin_action('speak')
        before=self.c.state()
        p=action(before,'speak');p['request_id']=pending['request_id']
        result=self.c.apply(p)
        self.assertEqual(self.c.state(),before)
        self.assertEqual(self.c.resume()['session']['scene'],result['narration'])
        self.assertEqual(self.c.resume()['session']['phase'],'awaiting_player')

    def test_resume_keeps_full_history_but_returns_bounded_recent_context(self):
        self.c.open_scene('Beginning.')
        for _ in range(8):
            pending=self.c.begin_action('wait')
            p=action(self.c.state(),'wait');p['request_id']=pending['request_id']
            self.c.apply(p)
        self.assertEqual(self.c.resume()['exchange_count'],17)
        self.assertEqual(len(self.c.resume()['recent_exchanges']),12)
        self.assertEqual(len(self.c.resume(limit=200)['recent_exchanges']),17)

    def test_active_selection_requires_explicit_demo_and_confines_paths(self):
        local=Path(self.temp.name)
        directory=local/'campaigns';directory.mkdir()
        c=Campaign(directory/'demo.sqlite').create(initial_state(),configuration())
        selection=directory/'active.json'
        selection.write_text('{"campaign_path":null}',encoding='utf-8')
        with patch('novel_engine.resume.ROOT',local):
            self.assertEqual(resume_active(selection)['status'],'no_active_campaign')
            with self.assertRaises(ValueError):select_campaign(c.path,selection=selection)
            c.open_scene('Exact resume point.')
            select_campaign(c.path,allow_demo=True,selection=selection)
            self.assertEqual(resume_active(selection)['session']['scene'],'Exact resume point.')
            selection.write_text('{"campaign_path":"../campaign.sqlite","kind":"demo"}',encoding='utf-8')
            with self.assertRaises(ValueError):resume_active(selection)

    def test_a_relative_selection_is_read_from_the_workspace_not_the_current_folder(self):
        local=Path(self.temp.name)
        directory=local/'campaigns'/'other';directory.mkdir(parents=True)
        c=Campaign(directory/'campaign.sqlite').create(initial_state(),configuration())
        c.open_scene('Other campaign.')
        selection=local/'campaigns'/'active.json'
        with patch('novel_engine.resume.ROOT',local), patch('os.getcwd',return_value=str(ROOT)):
            select_campaign('campaigns/other/campaign.sqlite',allow_demo=True,selection=selection)
            self.assertEqual(resume_active(selection)['session']['scene'],'Other campaign.')
            with self.assertRaisesRegex(ValueError,'Existing save expected'):
                select_campaign('campaigns/missing/campaign.sqlite',allow_demo=True,selection=selection)

    def test_prepare_turn_saves_once_and_returns_one_consistent_snapshot(self):
        self.c.open_scene('The guard waits.')
        before=self.c.state()
        with patch.object(self.c,'transaction',wraps=self.c.transaction) as transactions:
            prepared=self.c.prepare_turn('I talk to the guard.')
        self.assertEqual(transactions.call_count,1)
        pending=prepared['resume']['session']['pending_action']
        self.assertEqual(prepared['status'],'ready')
        self.assertEqual(prepared['state'],before)
        self.assertEqual(prepared['context']['player_action'],pending['text'])
        self.assertEqual(prepared['model_proposal']['request_id'],pending['request_id'])
        self.assertEqual(prepared['model_proposal']['expected_state_version'],before['state_version'])
        self.assertEqual(prepared['context']['state_version'],before['state_version'])
        self.assertEqual(Campaign(self.c.path).prepare_turn()['model_proposal'],prepared['model_proposal'])
        self.assertEqual(self.c.prepare_turn('I talk to the guard.')['resume']['exchange_count'],2)
        with self.assertRaises(ValueError):self.c.prepare_turn('Another action.')

    def test_prepare_turn_without_input_does_not_create_action_or_advance(self):
        self.c.open_scene('Scene saved.')
        before=self.c.resume()
        prepared=self.c.prepare_turn()
        self.assertEqual(prepared['status'],'no_action')
        self.assertIsNone(prepared['model_proposal'])
        self.assertEqual(prepared['resume'],before)
        self.assertEqual(self.c.resume(),before)

    def test_prepare_turn_rolls_back_input_when_context_fails(self):
        self.c.open_scene('Scene saved.')
        before=self.c.resume()
        with patch.object(ContextBuilder,'build_from_snapshot',side_effect=ValueError('Contexte unavailable')):
            with self.assertRaises(ValueError):self.c.prepare_turn('Action to save.')
        self.assertEqual(self.c.resume(),before)
        with self.assertRaises(ValueError):self.c.prepare_turn('Action to save.',exchange_limit=0)
        with self.assertRaises(ValueError):self.c.prepare_turn('Action to save.',context_limit=101)
        self.assertEqual(self.c.resume(),before)

    def test_prepare_turn_after_background_change_uses_current_version(self):
        self.c.open_scene('At the gates.')
        pending=self.c.begin_action('leave')
        before=self.c.state()
        self.c.apply(handle_background(task(before),before,threading.Event()),background=True)
        prepared=self.c.prepare_turn()
        self.assertEqual(prepared['model_proposal']['request_id'],pending['request_id'])
        self.assertEqual(prepared['model_proposal']['expected_state_version'],1)
        self.assertEqual(prepared['state']['state_version'],1)
        self.assertEqual(prepared['context']['state_version'],1)

    def test_prepare_turn_requires_explicit_reevaluation_after_rejection(self):
        self.c.open_scene('At the gates.')
        prepared=self.c.prepare_turn('leave')
        p=action(prepared['state'],'leave')
        p['request_id']=prepared['model_proposal']['request_id']
        self.c.apply(handle_background(task(prepared['state']),prepared['state'],threading.Event()),background=True)
        self.assertEqual(self.c.apply(p)['status'],'rejected')
        rejected=self.c.prepare_turn('leave')
        self.assertEqual(rejected['status'],'rejected_action')
        self.assertIsNone(rejected['model_proposal'])
        self.assertEqual(rejected['previous_result']['status'],'rejected')
        retried=self.c.prepare_turn(reevaluate=True)
        self.assertEqual(retried['status'],'ready')
        self.assertNotEqual(retried['model_proposal']['request_id'],p['request_id'])
        self.assertEqual(retried['context']['player_action'],'leave')
        self.assertEqual(retried['resume']['exchange_count'],2)

    def test_prepare_cli_and_apply_stdin_preserve_unicode_and_exact_narration(self):
        self.c.open_scene('At the door.')
        command=[sys.executable,str(ROOT/'novel_engine/scripts/campaign_cli.py'),'--campaign',str(self.c.path)]
        process=subprocess.run(command+['prepare-turn','--text','departure'],
                               capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(process.returncode,0,process.stderr)
        self.assertEqual(len(process.stdout.splitlines()),1)
        prepared=json.loads(process.stdout)
        p=action(prepared['state'],'departure')
        p['request_id']=prepared['model_proposal']['request_id']
        p['narration']=('You move forward.\n\n'
                           '"Where are you going?"')
        for _ in range(2):
            applied=subprocess.run(command+['apply','--file','-','--short'],
                                   input=json.dumps(p,ensure_ascii=False),capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(applied.returncode,0,applied.stderr)
            result=json.loads(applied.stdout)
            self.assertEqual(result['status'],'recorded')
            self.assertNotIn('applied_events',result)
            self.assertEqual(result['narration'],p['narration'])
        self.assertEqual(self.c.resume()['session']['scene'],p['narration'])
        self.assertEqual(self.c.resume()['exchange_count'],3)
        self.assertEqual(self.c.state()['state_version'],1)

    def test_prepare_cli_defaults_to_active_campaign_and_refuses_absent_database(self):
        from novel_engine.scripts import campaign_cli
        output=io.StringIO()
        with patch.object(campaign_cli,'active_campaign',return_value=({},self.c)), \
             patch.object(sys,'argv',['campaign_cli.py','prepare-turn']), \
             patch.object(sys,'stdout',output):
            campaign_cli.main()
        self.assertEqual(json.loads(output.getvalue())['state']['campaign_id'],self.c.state()['campaign_id'])
        missing=Path(self.temp.name)/'missing.sqlite'
        process=subprocess.run([sys.executable,str(ROOT/'novel_engine/scripts/campaign_cli.py'),'--campaign',str(missing),
                                'prepare-turn','--text','leave'],capture_output=True,text=True,encoding='utf-8')
        self.assertNotEqual(process.returncode,0)
        self.assertFalse(missing.exists())


if __name__=='__main__':
    unittest.main(verbosity=2)
