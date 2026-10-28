"""Staging deadlines requested by the player: counted replies and mandatory manifestation."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from novel_engine import scene_pacing
from novel_engine.agenda import Agenda
from novel_engine.campaign import Campaign
from novel_engine.demo import initial_state, configuration
from novel_engine.narrative import NarrativeLoop
from test_planner import FakeNarrator

DIRECTIVE = {'entry_id': 'meeting', 'after_seq': 1, 'max_replies': 3}
ENTRY = {'entry_id': 'meeting', 'expected_revision': -1, 'title': 'Requested meeting', 'actor': 'envoy',
         'location': 'town', 'kind': 'appointment', 'due_minute': 30, 'conditions': [],
         'references': ['demo:initial_configuration'], 'audience': ['narrator', 'player'], 'interrupt': False,
         'status': 'planned', 'reason': 'The player asked for it.', 'outcome_event_index': None}


class SceneFixture(unittest.TestCase):
    def setUp(self):
        base = ROOT / '.tmp-tests'
        base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        config = configuration()
        config['scene_pacing'] = [deepcopy(DIRECTIVE)]
        self.c = Campaign(Path(self.temp.name) / 'pacing.sqlite').create(initial_state(), config)
        self.c.open_scene('The envoy is expected.')
        with self.c.transaction() as con:
            agenda = Agenda(con)
            agenda.initialize()
            agenda._put(deepcopy(ENTRY), 'fixture', 0)
        self.narrator = FakeNarrator()

    def tearDown(self):
        self.temp.cleanup()

    def packet(self):
        config = self.c.config()
        with self.c.transaction() as con:
            return scene_pacing.packet(con, config)

    def play(self, count):
        loop = NarrativeLoop(self.c, self.narrator)
        for number in range(count):
            self.assertEqual(loop.run_turn(f'A small clarification {number}.')['status'], 'recorded')


class PacketTests(SceneFixture):
    def test_counts_only_played_narrator_replies_after_the_request(self):
        first = self.packet()[0]
        self.assertEqual((first['answers_since_request'], first['answer_rank_to_produce'],
                          first['remaining_before_limit']), (0, 1, 3))
        self.assertEqual(first['manifestation_date'], 30)
        self.assertEqual({key: first[key] for key in DIRECTIVE}, DIRECTIVE)
        self.play(2)
        later = self.packet()[0]
        self.assertEqual((later['answers_since_request'], later['answer_rank_to_produce'],
                          later['remaining_before_limit']), (2, 3, 1))

    def test_remaining_replies_never_go_negative(self):
        self.play(2)
        with self.c.transaction() as con:
            for number in range(3):
                con.execute('INSERT INTO exchanges(message_id,role,content) VALUES (?,?,?)',
                            (f'extra-{number}', 'narrator', 'Another played reply.'))
        late = self.packet()[0]
        self.assertEqual((late['answers_since_request'], late['remaining_before_limit']), (5, 0))

    def test_finished_missing_or_absent_entries_produce_no_directive(self):
        with self.c.transaction() as con:
            Agenda(con)._put({**ENTRY, 'status': 'finished', 'outcome_event_index': 0}, 'fixture', 0, 1)
        self.assertEqual(self.packet(), [])
        with self.c.transaction() as con:
            self.assertEqual(scene_pacing.packet(con, {}), [])
            self.assertEqual(scene_pacing.packet(con, {'scene_pacing': [{**DIRECTIVE, 'entry_id': 'unknown'}]}), [])

    def test_directive_reaches_the_prompt_and_its_instruction_forbids_substitutes(self):
        prepared = self.c.prepare_turn('I wait for the envoy.', narrative_memory=True)
        packet = json.loads(NarrativeLoop(self.c, self.narrator).build_prompt(prepared).rsplit('\nCURRENT TURN\n', 1)[1])
        directive = packet['scene_pacing'][0]
        self.assertEqual(directive['entry_id'], 'meeting')
        self.assertIn('postponement does not replace', directive['instruction'])
        self.assertNotIn(chr(0x2014), directive['instruction'])

    def test_no_directive_means_no_block_in_the_prompt(self):
        with self.c.transaction() as con:
            config = json.loads(con.execute('SELECT config FROM campaign WHERE id=1').fetchone()[0])
            config.pop('scene_pacing')
            con.execute('UPDATE campaign SET config=? WHERE id=1', (json.dumps(config),))
        prepared = self.c.prepare_turn('I wait for the envoy.', narrative_memory=True)
        packet = json.loads(NarrativeLoop(self.c, self.narrator).build_prompt(prepared).rsplit('\nCURRENT TURN\n', 1)[1])
        self.assertNotIn('scene_pacing', packet)


class ValidationTests(unittest.TestCase):
    def directive(self, rank, maximum=3):
        return {**DIRECTIVE, 'max_replies': maximum, 'answer_rank_to_produce': rank}

    def proposal(self, audience=('narrator', 'player')):
        return {'events': [{'audience': list(audience)}]}

    def update(self, status, index=0):
        return {'entry_id': 'meeting', 'status': status, 'outcome_event_index': index}

    def test_postponement_or_cancellation_is_always_refused(self):
        for status in ('postponed', 'cancelled'):
            for rank in (1, 3):
                with self.subTest(status=status, rank=rank), self.assertRaisesRegex(ValueError, 'postponement'):
                    scene_pacing.validate([self.directive(rank)], self.proposal(), [self.update(status)])

    def test_before_the_limit_the_encounter_may_stay_planned(self):
        scene_pacing.validate([self.directive(1)], self.proposal(), [])
        scene_pacing.validate([self.directive(2)], self.proposal(), [self.update('planned', None)])

    def test_at_the_limit_the_encounter_must_manifest_to_the_player(self):
        with self.assertRaisesRegex(ValueError, 'limit requires'):
            scene_pacing.validate([self.directive(3)], self.proposal(), [])
        with self.assertRaisesRegex(ValueError, 'limit requires'):
            scene_pacing.validate([self.directive(3)], self.proposal(), [self.update('planned')])
        for status in ('done', 'finished'):
            scene_pacing.validate([self.directive(3)], self.proposal(), [self.update(status)])

    def test_manifestation_event_must_exist_and_be_perceivable(self):
        for index in (None, 1, -1, True):
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, 'perceivable'):
                scene_pacing.validate([self.directive(3)], self.proposal(), [self.update('done', index)])
        with self.assertRaisesRegex(ValueError, 'perceivable'):
            scene_pacing.validate([self.directive(3)], self.proposal(('narrator',)), [self.update('done')])

    def test_each_directive_is_checked_independently(self):
        other = {**self.directive(3), 'entry_id': 'other'}
        with self.assertRaises(ValueError):
            scene_pacing.validate([self.directive(1), other], self.proposal(), [self.update('done')])


if __name__ == '__main__':
    unittest.main()
