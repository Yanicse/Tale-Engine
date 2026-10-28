"""Competing proofs and NPC sheets on fictional data, with no remote call."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from novel_engine.campaign import Campaign
from novel_engine.contracts import ROOT
from novel_engine.creations import CreationRegistry
from novel_engine.demo import initial_state, configuration
from novel_engine.context_diagnostic import build
from novel_engine.context_diagnostic import read
from novel_engine.npc_sheets import project
from novel_engine.indexed_memory import MemoryIndex
from novel_engine.narrative import NarrativeLoop, packet_references


def npc(ident='lina', **overrides):
    return dict(entity_id=ident, name='Lina', aliases=['The tracker'], kind='npc', origin='campaign',
        status='established', traits=['Parle soberly.', 'Current goal: Find her caravan again.',
                                  'Current attitude: Wary of the player.'],
        unknowns=['Destination unknown.'], links=[], audience=['narrator','player'],
        references=['exchange:1']) | overrides


class MemoryCompetition(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(':memory:')
        self.con.row_factory = sqlite3.Row
        self.con.execute('CREATE TABLE exchanges(seq INTEGER PRIMARY KEY, role TEXT, content TEXT)')
        self.index = MemoryIndex(self.con)
        self.index.initialize()

    def tearDown(self):
        self.con.close()

    def insert(self, ref, text, kind='event', seq=None, audience=None):
        self.index._insert(ref, kind, text, audience or ['narrator','player'], seq=seq,
                           event_order=seq, world_minute=0)

    def test_precise_dialogue_beats_general_topic_under_small_budget(self):
        self.insert('general_report', 'The guild keeps an old file on its members.')
        self.insert('exchange:9', "The guild keeps the copper contract at Soline's.", 'exchange', 9)
        packet = self.index.packet('Where is the copper contract?', previous='The guild waits.', budget=65)
        self.assertIn('exchange:9', [p['reference'] for p in packet['proofs']])
        self.assertLessEqual(packet['search']['characters'], 65)
        discarded = {d['reference']:d for d in packet['search']['diagnostic']['sources']}
        self.assertEqual(discarded['general_report']['reason'], 'memory_budget')

    def test_requested_reference_has_priority_over_newer_results(self):
        self.insert('exact_archive', 'The contract is announced but not accepted.')
        self.insert('recent_archive', 'The signed contract is stored in the copper chest.')
        packet = self.index.packet('signed contract copper chest', query='exact_archive', budget=55)
        self.assertEqual([p['reference'] for p in packet['proofs']], ['exact_archive'])
        self.assertIn('not accepted', packet['proofs'][0]['text'])

    def test_neighbors_cannot_evict_other_primary_evidence(self):
        self.insert('exchange:3', 'Soline keeps the contract.', 'exchange', 3)
        self.insert('exchange:9', 'The contract is worth fifty coins.', 'exchange', 9)
        self.con.execute('INSERT INTO exchanges VALUES (2,?,?)', ('player','Very long question. '*10))
        packet = self.index.packet('Soline contract fifty', budget=70)
        self.assertEqual(len(packet['proofs']), 2)
        self.assertLessEqual(packet['search']['characters'], 70)
        self.assertEqual(packet['search']['diagnostic']['neighbors'][0]['reason'], 'memory_budget')

    def test_private_proof_and_neighbor_absent_from_player_diagnostic(self):
        self.insert('secret_copper', 'Soline hides the copper contract.', audience=['narrator'])
        self.insert('exchange:3', 'Soline talks about the contract.', 'exchange', 3)
        self.insert('exchange:5', 'A secret about copper.', 'exchange', 5, ['narrator'])
        packet = self.index.packet('Soline copper contract', recipient='player')
        self.assertNotIn('secret_copper', json.dumps(packet))
        self.assertNotIn('exchange:5', json.dumps(packet))

    def test_long_unpunctuated_archive_keeps_matching_passage(self):
        self.insert('long_archive', 'Introduction '*300 + ' contract copper not accepted ' + 'fin '*20)
        packet = self.index.packet('contract copper', excerpt_chars=180, budget=180)
        self.assertIn('contract copper not accepted', packet['proofs'][0]['text'])
        self.assertTrue(packet['proofs'][0]['partial_excerpt'])

    def test_final_diagnostic_requires_text_not_just_reference_metadata(self):
        selection = {'sources':[{'reference':'secret_ref','status':'kept'}]}
        packet = {'memory_proofs':[{'reference':'secret_ref'}], 'memory_references':['secret_ref']}
        before = deepcopy(packet)
        diagnostic = build(packet, selection)
        self.assertFalse(diagnostic['memory'][0]['text_sent'])
        self.assertEqual(diagnostic['sent_memory_references'], [])
        self.assertEqual(packet, before)
        packet['recalled_exchanges'] = [{'seq':9,'role':'narrator','content':'The proof.'}]
        self.assertTrue(build(packet, {'sources':[{'reference':'exchange:9','status':'kept'}]})['memory'][0]['text_sent'])

    def test_final_omission_diagnostic_preserves_projection_reasons(self):
        selection = {'sheets':[{'entity_id':'lina','status':'kept'}]}
        result = build({}, npc=selection, omitted=['npc_sheets','omitted_entities_index'],
                       omitted_entities={'relations':['old_contact']})
        self.assertFalse(result['npc']['sheets'][0]['sent'])
        self.assertEqual(result['npc']['sheets'][0]['final_reason'], 'prompt_budget')
        self.assertEqual(result['omitted_entities'], {'relations':['old_contact']})


class NpcProjection(unittest.TestCase):
    def test_dynamic_fields_unknowns_and_no_mutation(self):
        value = npc()
        before = deepcopy(value)
        sheets, _ = project({}, [value], 'I question Lina.')
        self.assertEqual(sheets[0]['current_goal'], 'Find her caravan again.')
        self.assertEqual(sheets[0]['declared_attitude'], 'Wary of the player.')
        self.assertEqual(sheets[0]['traits'], ['Parle soberly.'])
        self.assertEqual(sheets[0]['unknowns'], ['Destination unknown.'])
        self.assertEqual(value, before)

    def test_missing_fields_remain_unknown_and_status_is_preserved(self):
        sheets, _ = project({}, [npc(status='testimony', traits=['Claims to be a messenger.'])], 'Lina')
        self.assertIsNone(sheets[0]['current_goal'])
        self.assertEqual(sheets[0]['status'], 'testimony')

    def test_explicit_name_beats_recent_scene_limit_and_budget(self):
        values = [npc(), npc('marie', name='Marie', aliases=[])]
        sheets, diag = project({}, values, 'Marie', 'Lina waits.', limit=1)
        self.assertEqual([s['entity_id'] for s in sheets], ['marie'])
        self.assertEqual(diag['sheets'][1]['reason'], 'sheet_limit')
        self.assertEqual(project({}, values, 'Lina', budget=1)[0], [])

    def test_private_alias_and_substring_do_not_select_public_sheet(self):
        sheets, diag = project({}, [npc(audience=['narrator'])], 'The tracker', recipient='player')
        self.assertEqual((sheets, diag['sheets']), ([], []))
        self.assertEqual(project({}, [npc()], 'The hillside is green.')[0], [])

    def test_direction_and_private_relation_filter(self):
        state = {'relations':{
            'player_lina':{'from_id':'player','to_id':'lina','attitude':'friendly',
                'references':['relation:1'],'audience':['narrator','player']},
            'lina_secret':{'from_id':'lina','to_id':'spy','attitude':'hostile',
                'references':['secret:1'],'audience':['narrator']}}}
        sheets, _ = project(state, [npc()], 'Lina', recipient='player')
        self.assertEqual(len(sheets[0]['relations']), 1)
        self.assertEqual(sheets[0]['relations'][0]['from_id'], 'player')
        self.assertNotIn('secret:1', json.dumps(sheets))
        self.assertIn('Wary', sheets[0]['declared_attitude'])

    def test_unmentioned_npc_is_not_selected_by_shared_trait(self):
        self.assertEqual(project({}, [npc()], 'I am looking for a caravan.')[0], [])


class PromptIntegration(unittest.TestCase):
    def setUp(self):
        (ROOT/'.tmp-tests').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp-tests')
        self.c = Campaign(Path(self.temp.name)/'novel_engine.sqlite').create(initial_state('test_npc'), configuration())
        self.c.open_scene('Lina waits at the door.')
        with self.c.transaction() as con:
            CreationRegistry(con).initialize()
            con.execute('INSERT INTO creation_revisions(entity_id,request_id,payload) VALUES (?,?,?)',
                        ('lina','fixture',json.dumps(npc())))

    def tearDown(self):
        self.temp.cleanup()

    def test_current_revision_and_prompt_diagnostic_without_model(self):
        with self.c.transaction() as con:
            value = npc(traits=['Parle soberly.', 'Current goal: Wait for the merchant.'])
            con.execute('INSERT INTO creation_revisions(entity_id,request_id,payload) VALUES (?,?,?)',
                        ('lina','revision',json.dumps(value)))
        before = self.c.state()
        prepared = self.c.prepare_turn('I talk to Lina.', narrative_memory=True)
        untouched = deepcopy(prepared)
        loop = NarrativeLoop(self.c, None)
        packet = json.loads(loop.build_prompt(prepared).rsplit('\nCURRENT TURN\n',1)[1])
        self.assertEqual(prepared, untouched)
        self.assertEqual(self.c.state(), before)
        self.assertEqual(packet['npc_sheets'][0]['current_goal'], 'Wait for the merchant.')
        self.assertNotIn('Find her caravan again.', json.dumps(packet['npc_sheets']))
        self.assertNotIn('diagnostic', packet['search_memory'])
        self.assertTrue(loop.last_metrics['context_diagnostic']['memory'])
        self.assertEqual(set(packet['allowed_references']), packet_references(packet))
        self.assertEqual(sum(b['utf8_bytes'] for b in loop.last_metrics['context_blocks'].values()), len(loop.build_prompt(prepared).encode()))
        with self.c.transaction() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM creation_revisions').fetchone()[0], 2)

    def test_sheet_source_is_authorized_even_when_registry_packet_excluded_it(self):
        prepared = self.c.prepare_turn('Hello.', narrative_memory=True)
        self.assertEqual(prepared['campaign_creations'], [])
        self.assertTrue(prepared['npc_sheets'])
        packet = json.loads(NarrativeLoop(self.c,None).build_prompt(prepared).rsplit('\nCURRENT TURN\n',1)[1])
        self.assertIn('exchange:1', packet['memory_references'])

    def test_saved_diagnostic_read_is_readonly_and_public_output_hides_it(self):
        import contextlib
        import io
        from unittest.mock import patch
        from novel_engine.scripts.diagnose_context import main as diagnostic_main
        from novel_engine.scripts.play_narrative import main
        from test_narrative import FakeNarrator
        narrator = FakeNarrator()
        with patch('novel_engine.narration_codex.CodexNarrator', return_value=narrator), \
             patch('novel_engine.planner.CodexPlanner'), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['--campaign',str(self.c.path),'--text','I talk to Lina.','--engine','codex','--json']), 0)
        public = json.loads(output.getvalue())
        self.assertNotIn('context_diagnostic', public['measures'])
        before = self.c.path.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(diagnostic_main([str(self.c.path)]), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result['status'], 'available')
        self.assertEqual(result['diagnostic']['npc']['sheets'][0]['entity_id'], 'lina')
        self.assertEqual(self.c.path.read_bytes(), before)
        self.assertEqual(read(self.c.path, 'not_found')['status'], 'absent')
        self.assertEqual(narrator.calls, 1)

    def test_prompt_budget_omission_removes_sheet_only_references(self):
        with self.c.transaction() as con:
            index = MemoryIndex(con)
            index.sync()
            index._insert('archive_soline', 'exchange', 'Unrelated archive.', ['narrator','player'], seq=99999)
        prepared = self.c.prepare_turn('Hello.', narrative_memory=True)
        prepared['npc_sheets'][0]['references'] = ['archive_soline']
        loop = NarrativeLoop(self.c, None)
        initial = loop.build_prompt(prepared)
        self.assertIn('archive_soline', json.loads(initial.rsplit('\nCURRENT TURN\n',1)[1])['memory_references'])
        loop.context_budget_bytes = len(initial.encode()) - 200
        packet = json.loads(loop.build_prompt(prepared).rsplit('\nCURRENT TURN\n',1)[1])
        self.assertNotIn('npc_sheets', packet)
        self.assertNotIn('archive_soline', packet['allowed_references'])
        self.assertNotIn('archive_soline', packet['memory_references'])
        self.assertEqual(loop.last_metrics['context_diagnostic']['npc']['sheets'][0]['final_reason'], 'prompt_budget')


if __name__ == '__main__':
    unittest.main()
