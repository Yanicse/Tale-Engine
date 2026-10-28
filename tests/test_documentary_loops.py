"""Targeted search, knowledge transmission and persistent budgets, offline."""
from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from novel_engine.campaign import Campaign
from novel_engine.canon_selection import CanonSelector, retain_context, public_references
from novel_engine.contracts import ROOT, check_json
from novel_engine.demo import initial_state, configuration
from novel_engine.narrative import NarrativeLoop
from novel_engine.recovery import RecoveryBudget
from novel_engine.narrative_preparation import select_modules
from novel_engine.scripts.fictional_canon_fixture import create_fixture
from test_planner import FakeNarrator, FakePlanner


class DocumentaryFixture(unittest.TestCase):
    def setUp(self):
        (ROOT/'.tmp-tests').mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'.tmp-tests')
        self.folder=Path(self.temp.name)
        self.snapshot=create_fixture(self.folder/'canon.sqlite')
        with closing(sqlite3.connect(self.snapshot)) as con, con:
            for ident,name,kind in [('cercle','Cercle Emeraude','faction'),('maera','Maera','character')]:
                con.execute('INSERT INTO entities VALUES (?,?,?,?)',(ident,kind,name,'named'))
                con.execute('INSERT INTO aliases VALUES (?,?)',(ident,name))
                con.execute('INSERT INTO canonical_entities VALUES (?,?)',(ident,ident))
            records=[('v01_p0001_0004:c1','cercle','Cercle Emeraude',
                      'The Emerald Circle has five adventurers.','composition'),
                     ('v01_p0001_0004:c2','maera','Maera',
                      'Maera leads the Emerald Circle, an adamantite-rank team.','direction'),
                     ('v01_p0001_0004:c3','maera','Maera',
                      'Maera is famous for her knowledge of the ancient trails.','reputation')]
            for ref,ident,name,text,predicate in records:
                con.execute('INSERT INTO consolidated_usable_claims VALUES (?,?,?,?,?,?,?)',
                            (ref,ident,text,predicate,None,'canon',ref))
                con.execute('INSERT INTO sources VALUES (?,1,1)',(ref,))
                con.execute('INSERT INTO consolidated_fts VALUES (?,?,?,?,?)',(ref,name,name,text,predicate))
        state=initial_state('documentary');state['versions'].update(canon_revision=125,canon_snapshot=str(self.snapshot))
        config=configuration();config['canon_selection']={'enabled':True,'version':1}
        self.c=Campaign(self.folder/'novel_engine.sqlite').create(state,config)
        self.c.open_scene('The innkeeper waits in Bright Town, near the local Guild.')
        self.n=FakeNarrator()

    def tearDown(self):self.temp.cleanup()

    def selector(self):return CanonSelector(self.c.state(),self.c.config())

    def budget(self):
        rid=self.c.resume()['session']['pending_action']['request_id']
        with self.c.transaction() as con:return RecoveryBudget(con,rid).state()

    def memory(self, query='exchange:1'):
        return {'narrative_schema_version':'1.2','narration':'','events':[], 'blocking_questions':[],
                'memory_updates':[],'agenda_updates':[],'continuation':None,'roll_checks':[],
                'memory_request':{'query':query,'reason':'Precise source essential.'}}

    def test_named_subject_beats_city_and_generic_guild_context(self):
        packet=self.selector().select('Which adventurer is known?',scene='The local Guild is in Bright Town.',
            position='Bright Town',query='canon: Maera, reputation and rank in Bright Town. Without importing a future episode.')
        self.assertTrue(packet['facts'])
        self.assertTrue(all(f['subject']=='Maera' or 'Maera' in f['statement'] for f in packet['facts']))
        self.assertTrue(packet['search']['request_satisfied'])
        self.assertLessEqual(len(check_json(packet)),3000)

    def test_composition_does_not_establish_reputation_or_chronology(self):
        # Remove the other mentions of the group to keep only a documentary composition.
        with closing(sqlite3.connect(self.snapshot)) as con, con:
            con.execute("DELETE FROM consolidated_fts WHERE entity_name='Maera'")
            con.execute("DELETE FROM consolidated_usable_claims WHERE canonical_subject_id='maera'")
        packet=self.selector().select(query='canon: public reputation of the Emerald Circle before T0')
        self.assertTrue(packet['facts'])
        self.assertFalse(packet['search']['request_satisfied'])
        self.assertEqual(packet['search']['diagnostic'],'partial_excerpts')

    def test_exact_reference_is_not_discarded_for_an_unrelated_place(self):
        packet=self.selector().select('The local Guild in Bright Town.',
                                    query='canon:v01_p0001_0004:c3')
        self.assertIn('canon:v01_p0001_0004:c3',[f['reference'] for f in packet['facts']])
        self.assertTrue(packet['search']['request_satisfied'])

    def test_missing_target_does_not_become_satisfied_by_generic_results(self):
        packet=self.selector().select(query='canon: reputation of the most famous adventurers in Bright Town')
        self.assertFalse(packet['search']['request_satisfied'])

    def test_references_survive_a_different_recovery_query_within_budget(self):
        rid=self.c.begin_action('Hello')['request_id']
        self.c.recover_memory(rid,'canon:v01_p0001_0004:c3')
        self.c.recover_memory(rid,'canon:v05_p0001_0004:c2')
        packet=self.c.prepare_turn(narrative_memory=True)['documentary_canon']
        refs=[f['reference'] for f in packet['facts']]
        self.assertIn('canon:v01_p0001_0004:c3',refs)
        self.assertIn('canon:v05_p0001_0004:c2',refs)
        self.assertEqual(len(refs),len(set(refs)))
        self.assertLessEqual(len(check_json(packet)),3000)

    def test_rephrasing_same_reference_is_no_progress_even_with_reset(self):
        rid=self.c.begin_action('Hello')['request_id']
        first=self.c.recover_memory(rid,'canon:v01_p0001_0004:c3')
        again=self.c.recover_memory(rid,'canon: reread exactly v01_p0001_0004:c3',reset=True)
        self.assertEqual(first['search_status'],'ready')
        self.assertEqual(again['search_status'],'to_clarify')
        self.assertEqual(self.budget()['searches'],2)
        third=self.c.recover_memory(rid,'canon: Maera',reset=True)
        self.assertEqual(third['search_status'],'blocked')
        self.assertEqual(self.budget()['searches'],2)

    def test_reference_already_supplied_is_not_new_evidence(self):
        prepared=self.c.prepare_turn('Maera',narrative_memory=True)
        rid=prepared['model_proposal']['request_id']
        ref=prepared['documentary_canon']['facts'][0]['reference']
        self.assertEqual(self.c.recover_memory(rid,ref)['search_status'],'to_clarify')

    def test_two_generations_max_despite_restart_retry_and_new_query(self):
        self.n.transform=lambda packet,result:self.memory()
        before=self.c.state();scene=self.c.resume()['session']['scene']
        first=NarrativeLoop(self.c,self.n).run_turn('I ask for a clarification.')
        self.assertEqual(first['status'],'required_memory')
        second=NarrativeLoop(Campaign(self.c.path),self.n).run_turn(resume=True)
        self.assertEqual(second['diagnostic_code'],'recovery_blocked')
        for options in ({},{'retry':True},{'query':'canon: Maera'},{'retry':True,'query':'Guilde'}):
            planner=FakePlanner();loop=NarrativeLoop(Campaign(self.c.path),self.n,planner=planner)
            result=loop.run_turn(resume=True,**options)
            self.assertEqual(result['status'],'required_memory')
            self.assertEqual(loop.last_metrics['model_calls'],0)
            self.assertEqual(planner.calls,0)
        self.assertEqual(self.n.calls,2)
        self.assertEqual(self.c.state(),before)
        self.assertEqual(self.c.resume()['session']['scene'],scene)
        self.assertEqual(self.budget()['generations'],2)

    def test_one_enriched_generation_can_finish_and_replay_without_call(self):
        self.n.transform=lambda packet,result:self.memory() if self.n.calls==1 else result
        loop=NarrativeLoop(self.c,self.n)
        loop.run_turn('I ask for a clarification.')
        result=loop.run_turn(resume=True)
        self.assertEqual(result['status'],'recorded')
        self.assertEqual(loop.run_turn(resume=True)['narration'],result['narration'])
        self.assertEqual(self.n.calls,2)

    def test_received_second_answer_is_reused_even_when_budget_is_exhausted(self):
        self.n.transform=lambda packet,result:self.memory() if self.n.calls==1 else result
        loop=NarrativeLoop(self.c,self.n);loop.run_turn('I ask for a clarification.')
        with patch.object(loop,'_proposal',side_effect=OSError('Panne locale')):
            with self.assertRaises(OSError):loop.run_turn(resume=True)
        self.assertEqual(NarrativeLoop(self.c,self.n).run_turn(resume=True)['status'],'recorded')
        self.assertEqual(self.n.calls,2)

    def test_legacy_diagnostics_restore_budget_without_new_generation(self):
        rid=self.c.begin_action('I ask for a clarification.')['request_id']
        with self.c.transaction() as con:
            con.execute('CREATE TABLE narrative_diagnostics(id INTEGER PRIMARY KEY,request_id TEXT,payload TEXT,error TEXT)')
            for i in range(6):
                con.execute('INSERT INTO narrative_diagnostics VALUES (?,?,?,?)',
                            (i,rid,check_json({'response':self.memory('canon: Maera, search '+str(i))}),''))
        loop=NarrativeLoop(self.c,self.n)
        result=loop.run_turn(resume=True)
        self.assertEqual(result['diagnostic_code'],'recovery_blocked')
        self.assertEqual(result['recovery_budget']['generations'],6)
        self.assertEqual(self.n.calls,0)

    def test_question_does_not_enable_every_module_as_an_unclassified_action(self):
        packet={'current_state':self.c.state(),'player_input':'And which adventurer is the best known?',
                'recent_exchanges':[],'world_agenda':{},'world_planning':{}}
        modules=select_modules(packet)
        self.assertNotIn('combat',modules)
        self.assertNotIn('abilities',modules)
        self.assertNotIn('possessions',modules)

    def test_both_prompt_paths_explain_learning_and_preserve_secrets(self):
        prepared=self.c.prepare_turn('Hello',narrative_memory=True)
        for compact in (True,False):
            prompt=NarrativeLoop(self.c,self.n).build_prompt(prepared,compact=compact)
            self.assertIn('can be learned during this dialogue',prompt)
            self.assertIn('Secrets stay secret',prompt)
            self.assertIn('a source compatible with the period',prompt)
            self.assertIn('do not hide a documentary blockage',prompt)

    def test_a_sourced_dialogue_can_teach_without_importing_a_past_episode(self):
        before=self.c.state()
        def answer(packet,result):
            ref=next(f['reference'] for f in packet['documentary_canon']['facts']
                     if 'welcomes adventurers' in f['statement'])
            result.update(narrative_schema_version='1.2',memory_request=None,agenda_updates=[],continuation=None,roll_checks=[],
                narration='The innkeeper explains that the local Guild welcomes adventurers in the center of town.',
                events=[{'world_minute':before['world_minute'],'description':'The innkeeper passes on a geographic hint.',
                         'causes':['current_action',ref],'conditions':[],'changes':[],
                         'audience':['narrator','player']}],
                memory_updates=[{'topic_id':'guild_hint','title':"Innkeeper's hint",
                                 'aliases':['Guilde locale'],'references':['current_turn']}])
            return result
        self.n.transform=answer
        result=NarrativeLoop(self.c,self.n).run_turn('I ask where the local Guild welcomes adventurers.')
        self.assertEqual(result['status'],'recorded',result)
        journal=self.c.journal()
        self.assertEqual(len(journal),1)
        self.assertIn('passes on',journal[0]['description'])
        self.assertEqual(self.c.state()['world_minute'],before['world_minute'])
        proofs=self.c.memories('guild_hint')['proofs']
        self.assertTrue(proofs)
        self.assertFalse(any(p['reference'].startswith('canon:') for p in proofs))

    def test_legacy_receipt_revalidation_does_not_count_as_another_generation(self):
        rid=self.c.begin_action('I ask for a clarification.')['request_id']
        loop=NarrativeLoop(self.c,self.n)
        loop._save_attempt(rid,'receipt',{'received':json.dumps(self.memory())})
        with self.c.transaction() as con:
            con.execute('CREATE TABLE narrative_diagnostics(id INTEGER PRIMARY KEY,request_id TEXT,payload TEXT,error TEXT)')
            for i in range(2):
                con.execute('INSERT INTO narrative_diagnostics VALUES (?,?,?,?)',
                            (i,rid,check_json({'response':self.memory()}),''))
            self.assertEqual(RecoveryBudget(con,rid).state()['generations'],1)




if __name__=='__main__':unittest.main()
