"""Explicit chronicle postponements in the private context, without bypassing validation."""
import json
import unittest

from novel_engine.agenda import Agenda, CHRONICLE_REPORT_DELAY
from novel_engine.instructions import CHRONICLE_REPORTS
from novel_engine.narrative import NarrativeLoop
import test_documentary_loops as fixtures


class ChronicleConstraintsTests(unittest.TestCase):
    tearDown = fixtures.DocumentaryFixture.tearDown

    def setUp(self):
        fixtures.DocumentaryFixture.setUp(self)
        self.entry = {'entry_id': 'chronicle:signal', 'expected_revision': -1, 'title': 'A sign to perceive',
            'actor': 'relay', 'location': 'town', 'kind': 'risk', 'due_minute': 60, 'conditions': [],
            'references': ['exchange:1'], 'audience': ['narrator'], 'interrupt': False, 'status': 'planned',
            'reason': 'Private manifestation to prepare.', 'outcome_event_index': None}
        with self.c.transaction() as con:
            Agenda(con).initialize()
            Agenda(con)._put(self.entry, 'fixture', 0)

    def report(self, revision):
        with self.c.transaction() as con:
            Agenda(con)._put({**self.entry, 'status': 'postponed', 'due_minute': 60+revision},
                             'report_fixture', 0, revision)

    def packet(self, recipient='narrator'):
        with self.c.transaction() as con:
            return Agenda(con).packet(self.c._state(con), recipient=recipient)

    def test_counts_come_from_history_and_forbid_third_report(self):
        for count in range(3):
            if count:
                self.report(count)
            with self.subTest(count=count):
                packet = self.packet()
                constraint = packet['chronicle_constraints'][self.entry['entry_id']]
                self.assertEqual(constraint['postponements_done'], count)
                self.assertEqual(constraint['postponements_left'], 2-count)
                self.assertEqual(constraint['postponement_allowed'], count < 2)
                self.assertEqual(constraint['postpone_by_minute'],
                                 60+count+CHRONICLE_REPORT_DELAY if count < 2 else None)
        with self.c.transaction() as con:
            agenda = Agenda(con)
            old = agenda.get(self.entry['entry_id'])
            with self.assertRaisesRegex(ValueError, 'twice'):
                agenda._check_chronicle(old, {**self.entry, 'status': 'postponed', 'due_minute': 70})
            agenda._check_chronicle(old, {**self.entry, 'status': 'finished'})

    def test_private_counters_do_not_reach_player_or_change_entries(self):
        self.report(1)
        with self.c.transaction() as con:
            before = con.execute('SELECT payload FROM world_agenda').fetchall()
            agenda = Agenda(con)
            self.assertEqual(agenda.chronicle_constraints([{'entry_id': 'world:followup'}]), {})
            self.assertEqual(con.execute('SELECT payload FROM world_agenda').fetchall(), before)
        packet = self.packet('player')
        self.assertEqual(packet['chronicle_constraints'], {})
        self.assertNotIn(self.entry['entry_id'], json.dumps(packet, ensure_ascii=False))

    def test_both_prompts_keep_counts_and_specific_instruction(self):
        self.report(1)
        self.report(2)
        prepared = self.c.prepare_turn('I wait patiently.', narrative_memory=True)
        for compact in (True, False):
            with self.subTest(compact=compact):
                prompt = NarrativeLoop(self.c, self.n).build_prompt(prepared, compact=compact)
                self.assertIn(CHRONICLE_REPORTS, prompt)
                packet = json.loads(prompt.rsplit('\nCURRENT TURN\n', 1)[1])
                constraint = packet['world_agenda']['chronicle_constraints'][self.entry['entry_id']]
                self.assertFalse(constraint['postponement_allowed'])
                self.assertEqual(constraint['postponements_left'], 0)
