"""The retry receives the structure errors, without rewriting the receipt or reloading the budget."""
from copy import deepcopy
import json
import unittest

from novel_engine.adapters import ModelUnavailable
from novel_engine.agenda import Agenda
from novel_engine.narrative import NarrativeLoop
from novel_engine.agenda_protocol import METADATA_CORRECTION, retry_metadata_correction
from novel_engine.recovery import RecoveryBudget
import test_documentary_loops as fixtures


class AgendaRetryTests(unittest.TestCase):
    tearDown = fixtures.DocumentaryFixture.tearDown

    def setUp(self):
        fixtures.DocumentaryFixture.setUp(self)
        self.entry = {'entry_id': 'craftsman:followup', 'expected_revision': -1, 'title': 'Check the models',
            'actor': 'craftsman', 'location': 'workshop', 'kind': 'project', 'due_minute': 300,
            'conditions': [], 'references': ['exchange:1'], 'audience': ['narrator'], 'interrupt': False,
            'status': 'planned', 'reason': 'Fictional scenario.', 'outcome_event_index': None}
        with self.c.transaction() as con:
            Agenda(con).initialize()
            Agenda(con)._put(self.entry, 'fixture', 0)
        creation = {key: deepcopy(self.entry[key]) for key in
                    ('title', 'actor', 'location', 'kind', 'conditions', 'audience', 'interrupt')}
        creation['title'] = 'Prepare the next step'
        command = {'entry_id': self.entry['entry_id'], 'status': 'planned', 'due_minute': None,
                   'reason': 'Consigne fictional.', 'references': ['agenda:'+self.entry['entry_id']],
                   'outcome_event_index': None, 'creation': creation}
        self.bad = fixtures.DocumentaryFixture.memory(self)
        self.bad.update(memory_request=None, narration='Rejected text.', agenda_updates=[command])
        self.n.transform = lambda packet, response: deepcopy(self.bad)
        self.loop = NarrativeLoop(self.c, self.n)

    def reject(self):
        before = self.c.state()
        with self.assertRaisesRegex(ModelUnavailable, 'metadata'):
            self.loop.run_turn('I ask for news.')
        self.assertEqual(self.c.state(), before)
        self.rid = self.c.resume()['session']['pending_action']['request_id']
        return self.loop._previous_attempt(self.rid)

    def test_explicit_retry_sends_private_correction_and_keeps_history_and_budget(self):
        old = self.reject()
        self.assertEqual(self.n.calls, 1)
        def verify(packet, response):
            correction = packet['agenda_command_correction']
            self.assertEqual(correction['commands_to_fix'], [{'index': 0, 'entry_id': self.entry['entry_id']}])
            self.assertEqual(correction['instruction'], METADATA_CORRECTION)
            return response
        self.n.transform = verify
        result = self.loop.run_turn(resume=True, retry=True)
        self.assertEqual(result['status'], 'recorded', result)
        self.assertEqual(result['request_id'], self.rid)
        self.assertEqual(self.n.calls, 2)
        with self.c.transaction() as con:
            budget = RecoveryBudget(con, self.rid)
            self.assertEqual(budget.state()['generations'], 2)
            self.assertTrue(budget.generation_exhausted())
            archived = con.execute('SELECT proposal FROM narrative_attempt_history WHERE request_id=?', (self.rid,)).fetchone()
            self.assertEqual(json.loads(archived[0])['received'], json.loads(old['proposal'])['received'])
            self.assertEqual(Agenda(con).get(self.entry['entry_id'])['title'], self.entry['title'])
        self.n.transform = lambda packet, response: (self.assertNotIn('agenda_command_correction', packet) or response)
        self.assertEqual(self.loop.run_turn('Merci.')['status'], 'recorded')

    def test_plain_resume_revalidates_old_receipt_without_call_or_correction(self):
        self.reject()
        with self.assertRaisesRegex(ModelUnavailable, 'metadata'):
            self.loop.run_turn(resume=True)
        self.assertEqual(self.n.calls, 1)
        self.assertIsNone(self.loop._agenda_retry_correction)

    def test_private_correction_survives_both_prompt_projections(self):
        old = self.reject()
        self.loop._agenda_retry_correction = retry_metadata_correction(json.loads(old['proposal']))
        prepared = self.c.prepare_turn(narrative_memory=True)
        for compact in (True, False):
            with self.subTest(compact=compact):
                prompt = self.loop.build_prompt(prepared, compact=compact)
                self.assertIn(METADATA_CORRECTION, prompt)
                packet = json.loads(prompt.rsplit('\nCURRENT TURN\n', 1)[1])
                self.assertEqual(packet['agenda_command_correction'], self.loop._agenda_retry_correction)
