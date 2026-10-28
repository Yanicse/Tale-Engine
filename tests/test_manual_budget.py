"""The manual engine writes a request, then reads an answer: only the reading uses the action's budget."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from novel_engine.adapters import AwaitingAnswer, ModelUnavailable
from novel_engine.campaign import Campaign
from novel_engine.demo import configuration, initial_state
from novel_engine.narration_manual import ManualNarrator
from novel_engine.narrative import NarrativeLoop
from novel_engine.recovery import RecoveryBudget

ANSWER = {'narrative_schema_version': '1.2', 'narration': 'You open the door.', 'events': [],
          'blocking_questions': [], 'memory_request': None, 'memory_updates': [], 'agenda_updates': [],
          'continuation': None, 'roll_checks': []}


class ManualBudgetTest(unittest.TestCase):
    def setUp(self):
        base = ROOT / '.tmp-tests'
        base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        folder = Path(self.temp.name)
        self.c = Campaign(folder / 'campaign.sqlite').create(initial_state(), configuration())
        self.c.open_scene('A door stands closed.')
        self.manual = folder / 'manual'
        self.loop = NarrativeLoop(self.c, ManualNarrator(self.manual))

    def generations(self):
        pending = self.c.resume()['session']['pending_action']
        with self.c.transaction() as con:
            return RecoveryBudget(con, pending['request_id']).state()['generations']

    def answer(self, value):
        (self.manual / 'turn.response.json').write_text(
            value if isinstance(value, str) else json.dumps(value), encoding='utf-8')

    def test_writing_the_request_is_not_a_generation(self):
        with self.assertRaisesRegex(ModelUnavailable, 'Manual engine'):
            self.loop.run_turn('I open the door.')
        self.assertEqual(self.generations(), 0)
        # Asking again without an answer changes nothing either, however many times.
        for _ in range(3):
            with self.assertRaisesRegex(ModelUnavailable, 'Manual engine'):
                self.loop.run_turn(resume=True, retry=True)
        self.assertEqual(self.generations(), 0)
        self.answer(ANSWER)
        result = self.loop.run_turn(resume=True, retry=True)
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(result['narration'], 'You open the door.')

    def test_one_invalid_answer_still_leaves_a_second_try(self):
        with self.assertRaises(ModelUnavailable):
            self.loop.run_turn('I open the door.')
        self.answer({'narration': 'Missing fields.'})
        with self.assertRaisesRegex(ModelUnavailable, 'invalid'):
            self.loop.run_turn(resume=True, retry=True)
        self.assertEqual(self.generations(), 1)
        self.answer(ANSWER)
        result = self.loop.run_turn(resume=True, retry=True)
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(self.c.state()['state_version'], 1)

    def test_two_invalid_answers_exhaust_the_budget_like_any_engine(self):
        with self.assertRaises(ModelUnavailable):
            self.loop.run_turn('I open the door.')
        for wrong in ('not json', 'still not json'):
            self.answer(wrong)
            with self.assertRaises(ModelUnavailable):
                self.loop.run_turn(resume=True, retry=True)
        self.answer(ANSWER)
        blocked = self.loop.run_turn(resume=True, retry=True)
        self.assertEqual(blocked['status'], 'required_memory')
        self.assertEqual(blocked['diagnostic_code'], 'recovery_blocked')
        self.assertEqual(self.c.state()['state_version'], 0)
        self.assertTrue(self.c.resume()['session']['pending_action'])

    def test_an_unchanged_refused_answer_is_not_read_or_charged_again(self):
        with self.assertRaises(ModelUnavailable):
            self.loop.run_turn('I open the door.')
        self.answer({'narration': 'Missing fields.'})
        with self.assertRaisesRegex(ModelUnavailable, 'refused|invalid'):
            self.loop.run_turn(resume=True, retry=True)
        self.assertEqual(self.generations(), 1)
        # Running the command again before fixing the file spends nothing and says what to do.
        for _ in range(3):
            with self.assertRaisesRegex(AwaitingAnswer, 'already read for this request'):
                self.loop.run_turn(resume=True, retry=True)
        self.assertEqual(self.generations(), 1)
        self.answer(ANSWER)
        self.assertEqual(self.loop.run_turn(resume=True, retry=True)['status'], 'recorded')

    def test_waiting_for_the_answer_is_not_reported_as_an_interruption(self):
        with self.assertRaises(AwaitingAnswer) as caught:
            self.loop.run_turn('I open the door.')
        self.assertIn('Waiting for a manual answer', str(caught.exception))
        self.assertNotIn('interrupted', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
