"""Encounters requested by the player: counted replies, and the encounter required by the limit reply."""
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
from novel_engine.agenda import Agenda, QUIET_NEWS_PREFIX
from novel_engine.campaign import Campaign
from novel_engine.demo import initial_state, configuration, event, proposal
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
        self.assertEqual((first['replies_since_request'], first['reply_number'],
                          first['remaining_before_limit']), (0, 1, 3))
        self.assertEqual(first['encounter_due_minute'], 30)
        self.assertEqual({key: first[key] for key in DIRECTIVE}, DIRECTIVE)
        self.play(2)
        later = self.packet()[0]
        self.assertEqual((later['replies_since_request'], later['reply_number'],
                          later['remaining_before_limit']), (2, 3, 1))

    def test_remaining_replies_never_go_negative(self):
        self.play(2)
        with self.c.transaction() as con:
            for number in range(3):
                con.execute('INSERT INTO exchanges(message_id,role,content) VALUES (?,?,?)',
                            (f'extra-{number}', 'narrator', 'Another played reply.'))
        late = self.packet()[0]
        self.assertEqual((late['replies_since_request'], late['remaining_before_limit']), (5, 0))

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
        return {**DIRECTIVE, 'max_replies': maximum, 'reply_number': rank}

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
        with self.assertRaisesRegex(ValueError, 'replies requires'):
            scene_pacing.validate([self.directive(3)], self.proposal(), [])
        with self.assertRaisesRegex(ValueError, 'replies requires'):
            scene_pacing.validate([self.directive(3)], self.proposal(), [self.update('planned')])
        for status in ('done', 'finished'):
            scene_pacing.validate([self.directive(3)], self.proposal(), [self.update(status)])

    def test_the_message_names_the_configured_limit(self):
        with self.assertRaisesRegex(ValueError, 'limit of 4 replies requires'):
            scene_pacing.validate([self.directive(4, maximum=4)], self.proposal(), [])

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


class QuietNewsTests(SceneFixture):
    INTERVAL = 360

    def setUp(self):
        super().setUp()
        with self.c.transaction() as con:
            config = json.loads(con.execute('SELECT config FROM campaign WHERE id=1').fetchone()[0])
            config['quiet_news'] = {'interval_minutes': self.INTERVAL, 'urgent_every': 3,
                                    'instruction': 'Prefer news from the harbor.'}
            con.execute('UPDATE campaign SET config=? WHERE id=1', (json.dumps(config),))
            con.execute('DELETE FROM world_agenda WHERE entry_id=?', (ENTRY['entry_id'],))
        self.now = self.c.state()['world_minute']

    def agenda(self):
        return self.c.world_agenda('narrator')['events']

    def play_at(self, minute, entry=None, moves=(), status='finished', audience=None):
        state = self.c.state()
        cause = ['agenda:' + entry['entry_id']] if entry else None
        fact = event(state, 'A moment in the story.', list(moves), minute=minute, causes=cause, audience=audience)
        updates = []
        continuation = None
        if entry:
            updates.append({k: v for k, v in entry.items() if k not in ('revision', 'reference', 'date')})
            updates[0].update(status=status, expected_revision=entry['revision'], outcome_event_index=0)
            if entry['interrupt']:
                continuation = {'goal': 'Continue the evening', 'remaining': 'Make a choice',
                                'status': 'suspended', 'event_index': 0}
        turn = proposal(state, 'A moment in the story.', [fact])
        return self.c.apply(turn, agenda_changes={'updates': updates, 'continuation': continuation, 'roll_checks': []})

    def first_news(self):
        self.assertEqual(self.play_at(self.now + 2)['status'], 'recorded')
        return self.agenda()[0]

    def test_news_is_scheduled_on_the_fictional_clock_and_stays_private(self):
        first = self.first_news()
        self.assertEqual((first['entry_id'], first['due_minute'], first['interrupt']),
                         (QUIET_NEWS_PREFIX + '1', self.now + 2 + self.INTERVAL, False))
        self.assertIn('Prefer news from the harbor.', first['reason'])
        self.assertEqual(self.c.world_agenda()['events'], [])
        guidance = self.c.world_agenda('narrator')['pacing_guidance']
        self.assertEqual(guidance['pack_instruction'], 'Prefer news from the harbor.')
        self.assertNotIn('pacing_guidance', self.c.world_agenda())
        for step in (3, 4, 5):
            self.assertEqual(self.play_at(self.now + step)['status'], 'recorded')
        self.assertEqual(self.agenda(), [first])

    def test_news_must_be_shown_to_the_player_and_cannot_be_moved(self):
        first = self.first_news()
        due = first['due_minute']
        self.assertIn('show the news', self.play_at(due + 1)['reason'])
        self.assertIn('not perceived', self.play_at(due, first, audience=['narrator'])['reason'])
        self.assertIn('Keep this pacing deadline', self.play_at(due, first, status='cancelled')['reason'])
        created = {**{k: v for k, v in first.items() if k not in ('revision', 'reference', 'date')},
                   'entry_id': QUIET_NEWS_PREFIX + '9', 'expected_revision': -1, 'due_minute': due + 5,
                   'status': 'planned', 'outcome_event_index': None}
        turn = proposal(self.c.state(), 'A moment in the story.', [])
        refused = self.c.apply(turn, agenda_changes={'updates': [created], 'continuation': None, 'roll_checks': []})
        self.assertIn('created by the pacing scheduler', refused['reason'])

    def test_every_third_delivered_news_asks_for_a_choice(self):
        self.first_news()
        for urgent in (False, False, True, False, False, True):
            entry = self.agenda()[0]
            self.assertEqual(entry['interrupt'], urgent)
            self.assertEqual(self.play_at(entry['due_minute'], entry)['status'], 'recorded')

    def test_a_fight_resets_the_news_deadline_until_it_ends(self):
        first = self.first_news()
        path = '/resources/player_combat_active'
        fight = self.play_at(first['due_minute'], moves=[{'operation': 'set', 'path': path, 'value': 1}])
        self.assertEqual(fight['status'], 'recorded', fight)
        self.assertEqual(self.agenda(), [])
        end = first['due_minute'] + 10
        self.assertEqual(self.play_at(end, moves=[{'operation': 'set', 'path': path, 'value': 0}])['status'], 'recorded')
        second = self.agenda()[0]
        self.assertEqual((second['entry_id'], second['due_minute']), (QUIET_NEWS_PREFIX + '2', end + self.INTERVAL))

    def test_nothing_is_scheduled_without_the_pack_option(self):
        with self.c.transaction() as con:
            config = json.loads(con.execute('SELECT config FROM campaign WHERE id=1').fetchone()[0])
            del config['quiet_news']
            con.execute('UPDATE campaign SET config=? WHERE id=1', (json.dumps(config),))
        self.assertEqual(self.play_at(self.now + 2)['status'], 'recorded')
        self.assertEqual(self.agenda(), [])
        self.assertNotIn('pacing_guidance', self.c.world_agenda('narrator'))


class QuietNewsOvertakenTests(unittest.TestCase):
    NEWS = QUIET_NEWS_PREFIX + '1'
    DUE = 100

    def state(self, fight=0):
        return {'resources': {'player_combat_active': fight}}

    def fact(self, minute, operations=(), changes=()):
        return {'world_minute': minute, 'mechanical_operations': list(operations), 'changes': list(changes)}

    def update(self, entry_id, index, status='done', interrupt=False):
        return {'entry_id': entry_id, 'outcome_event_index': index, 'status': status, 'interrupt': interrupt}

    def overtaken(self, events, updates=(), continuation=None, fight=0):
        return scene_pacing.quiet_news_overtaken(self.state(fight), events, list(updates), self.DUE, continuation)

    def test_nothing_at_all_does_not_overtake_the_news(self):
        self.assertFalse(self.overtaken([]))
        self.assertFalse(self.overtaken([self.fact(90)], [self.update(self.NEWS, 0)]))

    def test_a_fight_already_active_overtakes_the_news(self):
        self.assertTrue(self.overtaken([], fight=1))

    def test_a_combat_start_operation_counts_only_up_to_the_due_minute(self):
        start = {'kind': 'combat_start'}
        self.assertTrue(self.overtaken([self.fact(self.DUE - 5, [start])]))
        self.assertTrue(self.overtaken([self.fact(self.DUE, [start])]))
        self.assertFalse(self.overtaken([self.fact(self.DUE + 1, [start])]))
        self.assertFalse(self.overtaken([self.fact(self.DUE - 5, [{'kind': 'damage'}])]))

    def test_a_combat_flag_change_counts_like_a_combat_start(self):
        path = '/resources/player_combat_active'
        self.assertTrue(self.overtaken([self.fact(self.DUE, changes=[{'path': path, 'value': 1}])]))
        self.assertFalse(self.overtaken([self.fact(self.DUE + 1, changes=[{'path': path, 'value': 1}])]))
        self.assertFalse(self.overtaken([self.fact(self.DUE, changes=[{'path': path, 'value': 0}])]))
        self.assertFalse(self.overtaken([self.fact(self.DUE, changes=[{'path': '/resources/other', 'value': 1}])]))

    def test_a_suspended_continuation_on_another_event_overtakes_the_news(self):
        events = [self.fact(self.DUE - 10), self.fact(self.DUE)]
        news = [self.update(self.NEWS, 1)]
        self.assertTrue(self.overtaken(events, news, {'event_index': 0}))

    def test_a_continuation_on_the_news_outcome_does_not_overtake_it(self):
        events = [self.fact(self.DUE - 10), self.fact(self.DUE)]
        news = [self.update(self.NEWS, 1)]
        self.assertFalse(self.overtaken(events, news, {'event_index': 1}))

    def test_a_continuation_after_the_due_minute_or_out_of_range_does_not_overtake(self):
        events = [self.fact(self.DUE + 1)]
        self.assertFalse(self.overtaken(events, [], {'event_index': 0}))
        for index in (None, 5, -1, True, '0'):
            with self.subTest(index=index):
                self.assertFalse(self.overtaken([self.fact(self.DUE - 10)], [], {'event_index': index}))
        self.assertFalse(self.overtaken([self.fact(self.DUE - 10)], [], {}))

    def test_another_interrupting_update_overtakes_the_news_when_resolved_in_time(self):
        events = [self.fact(self.DUE - 10), self.fact(self.DUE)]
        for status in ('done', 'finished'):
            with self.subTest(status=status):
                self.assertTrue(self.overtaken(events, [self.update('alarm', 1, status, True)]))

    def test_other_updates_do_not_overtake_the_news(self):
        events = [self.fact(self.DUE - 10), self.fact(self.DUE + 1)]
        cases = {'after the due minute': self.update('alarm', 1, 'done', True),
                 'not interrupting': self.update('visit', 0, 'done', False),
                 'not resolved': self.update('alarm', 0, 'planned', True),
                 'postponed': self.update('alarm', 0, 'postponed', True),
                 'no outcome event': self.update('alarm', None, 'done', True),
                 'outcome out of range': self.update('alarm', 7, 'done', True),
                 'another quiet news': self.update(QUIET_NEWS_PREFIX + '2', 0, 'done', True)}
        for label, update in cases.items():
            with self.subTest(label):
                self.assertFalse(self.overtaken(events, [update]))

    def test_combat_starts_reads_operations_and_changes(self):
        self.assertFalse(scene_pacing.combat_starts({}))
        self.assertTrue(scene_pacing.combat_starts({'mechanical_operations': [{'kind': 'combat_start'}]}))
        self.assertTrue(scene_pacing.combat_starts(
            {'changes': [{'path': '/resources/player_combat_active', 'value': 1}]}))
        self.assertFalse(scene_pacing.combat_starts(
            {'changes': [{'path': '/resources/player_combat_active', 'value': 0}]}))


class QuietNewsOptionTests(unittest.TestCase):
    def test_option_is_reduced_to_valid_values(self):
        clean = scene_pacing.clean_quiet_news
        self.assertEqual(clean({'interval_minutes': 360}), {'interval_minutes': 360, 'urgent_every': 3})
        self.assertEqual(clean({'interval_minutes': 720, 'urgent_every': 0, 'instruction': '  Harbor  first. '}),
                         {'interval_minutes': 720, 'urgent_every': 0, 'instruction': 'Harbor first.'})
        for bad in (None, [], {}, {'interval_minutes': 5}, {'interval_minutes': '360'}, {'interval_minutes': True}):
            self.assertIsNone(clean(bad))

    def test_numbers_must_be_whole_and_inside_the_bounds(self):
        clean = scene_pacing.clean_quiet_news
        for value in (True, False, 360.0, '360', None, 29, 43201, -30):
            with self.subTest(interval=value):
                self.assertIsNone(clean({'interval_minutes': value}))
        self.assertEqual(clean({'interval_minutes': 30})['interval_minutes'], 30)
        self.assertEqual(clean({'interval_minutes': 43200})['interval_minutes'], 43200)

    def test_urgent_every_falls_back_to_three_when_unusable(self):
        clean = scene_pacing.clean_quiet_news
        for value in (True, False, '2', 2.0, None, -1, 101):
            with self.subTest(urgent_every=value):
                self.assertEqual(clean({'interval_minutes': 60, 'urgent_every': value})['urgent_every'], 3)
        for value in (0, 1, 100):
            self.assertEqual(clean({'interval_minutes': 60, 'urgent_every': value})['urgent_every'], value)

    def test_instruction_is_cut_at_400_characters_and_blank_ones_are_dropped(self):
        clean = scene_pacing.clean_quiet_news
        self.assertEqual(len(clean({'interval_minutes': 60, 'instruction': 'a' * 500})['instruction']), 400)
        self.assertEqual(clean({'interval_minutes': 60, 'instruction': 'a' * 400})['instruction'], 'a' * 400)
        for value in ('', '   \n', None, 7, ['text']):
            with self.subTest(instruction=value):
                self.assertNotIn('instruction', clean({'interval_minutes': 60, 'instruction': value}))
        self.assertNotIn('instruction', clean({'interval_minutes': 60}))


if __name__ == '__main__':
    unittest.main()
