"""Persistent creations: an invented detail is recorded as such; an offer or a testimony is not an acquired fact.

Ported from the scenarios of the source project that do not depend on its story: identities and replay, revisions,
offers that change nothing, private creations, explicit acceptance, rollback, restart, bounded recall.
"""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from novel_engine.adapters import ModelUnavailable
from novel_engine.campaign import Campaign
from novel_engine.creations import CreationRegistry
from novel_engine.demo import configuration, initial_state
from novel_engine.indexed_memory import MemoryIndex
from novel_engine.narration_codex import normalize_response, response_schema
from novel_engine.narrative import NarrativeLoop, packet_references
from novel_engine.planner import WorldPlanner
from novel_engine.schema_codex import compile_schema

WORKER = ROOT / 'tests' / 'process_worker.py'


class Narrator:
    def __init__(self):
        self.calls, self.last_metrics = 0, {}
        self.transform = lambda packet, answer: answer

    def generate(self, prompt):
        self.calls += 1
        packet = json.loads(prompt.rsplit('\nCURRENT TURN\n', 1)[1])
        answer = {'narrative_schema_version': '1.2', 'narration': 'The innkeeper answers.', 'events': [],
                  'blocking_questions': [], 'memory_request': None, 'memory_updates': [], 'agenda_updates': [],
                  'continuation': None, 'roll_checks': []}
        return self.transform(packet, answer)


def entity(ident='lina', **values):
    return dict(entity_id=ident, name=values.pop('name', 'Lina'), aliases=['Tracker'], kind='npc', origin='campaign',
                status='established', traits=['Known in the village.'], unknowns=['Level unknown.'], links=['village'],
                audience=['narrator', 'player'], references=['current_turn'], **values)


class Fixture(unittest.TestCase):
    def setUp(self):
        base = ROOT / '.tmp-tests'
        base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.c = Campaign(Path(self.temp.name) / 'campaign.sqlite').create(initial_state('creation'), configuration())
        self.c.open_scene('An innkeeper waits at the door.')
        self.n = Narrator()

    def configure(self, **values):
        with self.c.transaction() as con:
            config = json.loads(con.execute('SELECT config FROM campaign').fetchone()[0])
            config.update(values)
            con.execute('UPDATE campaign SET config=?', (json.dumps(config),))

    def create(self, entities, action='I ask for news.'):
        self.configure(creation_format='1')
        self.n.transform = lambda p, r: {**r, 'creation_schema_version': '1', 'creation_updates': entities,
                                         'narration': '. '.join(u['name'] + ': ' + '; '.join(u['traits']) for u in entities)}
        return NarrativeLoop(self.c, self.n).run_turn(action)

    def registry(self, query='', recipient='narrator'):
        with self.c.transaction() as con:
            return CreationRegistry(con).packet(query, recipient)


class RegistryTests(Fixture):
    def test_five_identities_are_recorded_and_a_replay_calls_nothing(self):
        result = self.create([entity('npc%d' % i, name='Name%d' % i) for i in range(5)])
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(len(self.registry()), 5)
        self.assertEqual(NarrativeLoop(self.c, self.n).run_turn(resume=True)['status'], 'recorded')
        self.assertEqual(self.n.calls, 1)
        for i in range(5):
            self.assertTrue(self.c.memories('npc%d' % i)['proofs'])

    def test_six_evolutions_keep_the_founding_source_and_every_revision(self):
        self.create([entity()])
        for i in range(6):
            updated = entity()
            updated['traits'] = ['Evolution %d' % i]
            self.create([updated])
        with self.c.transaction() as con:
            sources, _ = MemoryIndex(con)._topic_sources('creation:lina', 'player')
            self.assertIn('exchange:3', [s['reference'] for s in sources])
            self.assertEqual(con.execute('SELECT COUNT(*) FROM creation_revisions').fetchone()[0], 7)

    def test_an_offer_changes_no_state_agenda_project_or_planner_counter(self):
        self.c.prepare_turn()
        before, agenda = self.c.state(), self.c.world_agenda()
        with self.c.transaction() as con:
            WorldPlanner(con).initialize()
            planner = [tuple(r) for r in con.execute('SELECT * FROM planner_meta')]
        offer = entity('mission')
        offer.update(kind='quest', status='proposed')
        self.create([entity(), offer])
        after = self.c.state()
        for key in ('relations', 'commitments', 'projects', 'resources', 'world_minute'):
            self.assertEqual(before.get(key), after.get(key))
        self.assertEqual(self.c.world_agenda(), agenda)
        with self.c.transaction() as con:
            self.assertEqual([tuple(r) for r in con.execute('SELECT * FROM planner_meta')], planner)

    def test_a_private_creation_stays_invisible_to_the_player_even_after_a_rebuild(self):
        secret = entity('secret', name='Hidden identity')
        secret.update(aliases=['Secret alias'], audience=['narrator'])

        def hidden(packet, answer):
            answer.update(creation_schema_version='1', creation_updates=[secret])
            secret['references'] = ['current_event:0']
            answer['events'] = [dict(world_minute=0, description='Identity kept secret.', causes=['current_action'],
                                     conditions=[], changes=[], audience=['narrator'], asset_operations=[])]
            return answer
        self.configure(creation_format='1')
        self.n.transform = hidden
        self.assertEqual(NarrativeLoop(self.c, self.n).run_turn('I look around.')['status'], 'recorded')
        public = entity()
        public['links'] = ['secret']
        self.create([public])
        self.assertNotIn('secret', json.dumps(self.registry(recipient='player')))
        self.assertFalse(self.c.memories('Secret alias')['proofs'])
        self.assertTrue(self.c.memories('Lina')['proofs'])
        with self.c.transaction() as con:
            MemoryIndex(con).rebuild()
        self.assertNotIn('Secret alias', json.dumps(self.registry(recipient='player')))

    def test_a_quest_is_accepted_only_by_an_explicit_decision_and_replays_do_not_regress(self):
        quest = entity('mission')
        quest.update(kind='quest', status='proposed')
        self.create([quest])
        quest = deepcopy(quest)
        quest['status'] = 'accepted'
        accepted = self.create([quest], 'I accept the mission.')
        saved = json.loads(NarrativeLoop(self.c, self.n)._previous_attempt(accepted['request_id'])['proposal'])
        quest = deepcopy(quest)
        quest['status'] = 'resolved'
        self.create([quest], 'I report the result.')
        self.assertEqual(self.registry('mission')[0]['status'], 'resolved')
        self.assertEqual(self.c.state()['resources'], initial_state('creation')['resources'])
        before = self.c.state()
        replay = self.c.apply(saved['proposal'], creation_updates=saved['creation_updates'],
                              memory_updates=saved['memory_updates'], agenda_changes=saved['agenda_changes'])
        self.assertEqual(replay['status'], 'recorded')
        self.assertEqual(self.registry('mission')[0]['status'], 'resolved')
        self.assertEqual(self.c.state(), before)

    def test_acceptance_without_a_decision_is_refused_and_nothing_is_recorded(self):
        self.create([entity('a'), entity('b')])
        self.assertEqual(len(self.registry('Lina')), 2)
        quest = entity('mission')
        quest.update(kind='quest', status='accepted')
        with self.assertRaises(ModelUnavailable):
            self.create([quest], 'I ask for an offer.')
        self.assertFalse(self.registry('mission'))
        self.assertTrue(self.c.resume()['session']['pending_action'])

    def test_a_conditional_or_negative_reply_is_not_an_acceptance(self):
        quest = entity('mission')
        quest.update(kind='quest', status='proposed')
        self.create([quest])
        for text in ('I accept if the pay is good.', "I don't accept.", 'Do I accept?', 'Maybe I accept.'):
            with self.subTest(text=text):
                accepted = deepcopy(quest)
                accepted['status'] = 'accepted'
                with self.assertRaises(ModelUnavailable):
                    self.create([accepted], text)
                self.assertEqual(self.registry('mission')[0]['status'], 'proposed')
                with self.c.transaction() as con:
                    con.execute("UPDATE session SET payload=json_set(payload,'$.pending_action',NULL)")

    def test_questions_and_memory_requests_cannot_create(self):
        base = self.n.generate(NarrativeLoop(self.c, self.n).build_prompt(self.c.prepare_turn('Hello')))
        for extra in ({'blocking_questions': ['Do you know Lina?']},
                      {'memory_request': {'query': 'Lina', 'reason': 'Archive needed'}}):
            answer = {**base, **extra, 'creation_schema_version': '1', 'creation_updates': [entity()]}
            with self.assertRaises(ValueError):
                normalize_response(answer)
        self.assertFalse(self.registry())

    def test_format_must_be_installed_and_a_receipt_cannot_be_replayed_with_other_content(self):
        self.c.begin_action('Hello')
        proposal = self.c.prepare_turn()['model_proposal']
        proposal['narration'] = 'Lina waits.'
        before = self.c.state()
        with self.assertRaises(ValueError):
            self.c.apply(proposal, creation_updates=[entity()])
        self.assertEqual(self.c.state(), before)
        self.configure(creation_format='1')
        self.assertEqual(self.c.apply(proposal, creation_updates=[entity()])['status'], 'recorded')
        self.assertEqual(self.c.apply(proposal, creation_updates=[entity()])['status'], 'recorded')
        other = entity()
        other['name'] = 'Other'
        with self.assertRaises(ValueError):
            self.c.apply(proposal, creation_updates=[other])

    def test_creative_contract_is_independent_of_the_mechanics_version(self):
        for version in ('1.2', '1.3'):
            for creative in (True, False):
                schema = response_schema(version=version, creations=creative)
                compile_schema(schema)
                self.assertEqual('mechanical_operations' in schema['properties']['events']['items']['properties'],
                                 version == '1.3')
                self.assertEqual('creation_updates' in schema['properties'], creative)


class FailureAndRestartTests(Fixture):
    def test_a_failing_registry_write_rolls_the_turn_back_and_the_receipt_is_reused(self):
        self.configure(creation_format='1')
        self.n.transform = lambda p, r: {**r, 'creation_schema_version': '1', 'creation_updates': [entity()]}
        before = self.c.state()
        with patch.object(CreationRegistry, 'apply', side_effect=OSError('Disk unavailable')):
            with self.assertRaises(OSError):
                NarrativeLoop(self.c, self.n).run_turn('Hello')
        self.assertEqual(before, self.c.state())
        self.assertTrue(self.c.resume()['session']['pending_action'])
        self.assertEqual(NarrativeLoop(self.c, self.n).run_turn(resume=True)['status'], 'recorded')
        self.assertEqual(self.n.calls, 1)

    def test_a_failing_memory_index_leaves_no_creation_behind(self):
        self.configure(creation_format='1')
        self.n.transform = lambda p, r: {**r, 'creation_schema_version': '1', 'creation_updates': [entity()]}
        before = self.c.state()
        with patch.object(MemoryIndex, 'apply_updates', side_effect=OSError('Index failure')):
            with self.assertRaises(OSError):
                NarrativeLoop(self.c, self.n).run_turn('Hello')
        self.assertEqual(before, self.c.state())
        self.assertEqual(self.registry(), [])
        self.assertEqual(NarrativeLoop(self.c, self.n).run_turn(resume=True)['status'], 'recorded')
        self.assertEqual(self.n.calls, 1)

    def test_a_creation_survives_twenty_exchanges_a_restart_and_a_second_process(self):
        self.create([entity()])
        self.n.transform = lambda p, r: r
        for i in range(20):
            NarrativeLoop(self.c, self.n).run_turn('Thanks %d' % i)
        self.c = Campaign(self.c.path)
        for query in ('Lina', 'Tracker', 'village'):
            self.assertEqual(self.registry(query)[0]['entity_id'], 'lina')
        self.assertTrue(self.c.memories('Tracker')['proofs'])
        result = subprocess.run([sys.executable, str(WORKER), 'recall', str(self.c.path), 'Tracker'],
                                capture_output=True, text=True, check=True)
        recalled = json.loads(result.stdout)
        self.assertEqual(recalled['creations']['creations'][0]['entity_id'], 'lina')
        self.assertTrue(recalled['memory']['proofs'])

    def test_a_thousand_creations_stay_within_the_prompt_budget_and_the_exact_one_is_recalled(self):
        self.create([entity()])
        with self.c.transaction() as con:
            for i in range(1000):
                value = entity('ident%d' % i, name='Person %d' % i)
                value.update(aliases=['Nickname%d' % i], references=['exchange:3'])
                con.execute('INSERT INTO creation_revisions(entity_id,request_id,payload) VALUES (?,?,?)',
                            (value['entity_id'], 'fixture', json.dumps(value)))
        for query in ('ident999', 'Nickname999'):
            prepared = self.c.prepare_turn('I look for ' + query + '.', narrative_memory=True)
            loop = NarrativeLoop(self.c, self.n)
            prompt = loop.build_prompt(prepared)
            packet = json.loads(prompt.rsplit('\nCURRENT TURN\n', 1)[1])
            self.assertLess(len(prompt.encode('utf-8')), loop.context_budget_bytes)
            self.assertIn('ident999', [u['entity_id'] for u in packet['campaign_creations']])
            self.assertEqual(set(packet['allowed_references']), packet_references(packet))
            with self.c.transaction() as con:
                con.execute("UPDATE session SET payload=json_set(payload,'$.pending_action',NULL)")


if __name__ == '__main__':
    unittest.main()
