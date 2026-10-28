"""Reminders at the exact place, on fictional campaigns and local models only."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from novel_engine.adapters import ModelUnavailable
from novel_engine.campaign import Campaign
from novel_engine.contracts import ROOT, check
from novel_engine.demo import initial_state, configuration
from novel_engine.narrative import NarrativeLoop, packet_references
from novel_engine.place_reminders import project
from test_narrative import FakeNarrator


def agreement(**values):
    return {'description':'Hand over the plans, delivery not established.', 'status':'open',
            'references':['demo:initial_configuration'], 'audience':['narrator','player'],
            'reminder_location':'workshop'} | values


def entry(**values):
    return {'entry_id':'delivery', 'status':'planned', 'title':'Check the delivery.',
            'location':'workshop', 'audience':['narrator','player'], 'reference':'agenda:delivery',
            'references':['demo:initial_configuration'], 'revision':0, 'due_minute':9999,
            'conditions':[]} | values


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.state = initial_state('test_reminder')
        self.state['positions']['player'] = 'workshop'
        self.state['commitments'] = {'plans':agreement()}

    def test_exact_location_and_unknown_location_are_not_guessed(self):
        self.assertEqual(len(project(self.state, {}, {})[0]), 1)
        self.state['positions']['player'] = 'annex_workshop'
        self.assertEqual(project(self.state, {}, {})[0], [])
        self.state['positions']['player'] = 'workshop'
        self.state['commitments']['plans'].pop('reminder_location')
        self.state['commitments']['plans']['description'] = 'I promise a delivery to the workshop.'
        self.assertEqual(project(self.state, {}, {})[0], [])

    def test_old_agreement_can_use_its_exact_agenda_entry(self):
        self.state['commitments']['plans'].pop('reminder_location')
        agenda = {'events':[entry(entry_id='accord:plans', reference='agenda:accord:plans')]}
        output, _ = project(self.state, agenda, {})
        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]['agenda_followup'], 'accord:plans')
        self.assertIn('agenda:accord:plans', output[0]['references'])
        self.assertEqual(output[0]['planned_date'], 9999)
        self.state['commitments']['plans']['status'] = 'kept'
        self.assertEqual(project(self.state, agenda, {})[0], [])

    def test_private_agenda_cannot_supply_public_agreement_location(self):
        self.state['commitments']['plans'].pop('reminder_location')
        agenda = {'events':[entry(entry_id='accord:plans', audience=['narrator'])]}
        self.assertEqual(project(self.state, agenda, {}, recipient='player')[0], [])

    def test_seen_only_for_same_visit_and_same_source_version(self):
        output, _ = project(self.state, {}, {})
        session = {'place_reminders':{'place':'workshop', 'seen':{output[0]['id']:output[0]['fingerprint']}}}
        self.assertEqual(project(self.state, {}, session)[0], [])
        self.state['commitments']['plans']['description'] = 'A new step remains to be checked.'
        self.assertEqual(len(project(self.state, {}, session)[0]), 1)
        session['place_reminders']['place'] = 'place'
        self.assertEqual(len(project(self.state, {}, session)[0]), 1)

    def test_bounds_and_no_input_mutation(self):
        before = deepcopy(self.state)
        output, diag = project(self.state, {}, {}, budget=1)
        self.assertEqual(output, [])
        self.assertEqual(diag['reminders'][0]['reason'], 'reminder_budget')
        self.assertEqual(self.state, before)

    def test_conditions_and_inactive_projects(self):
        self.state['commitments'] = {}
        agenda = {'events':[entry(conditions=[{'path':'/positions/player','equals':'place'}])]}
        self.assertEqual(project(self.state, agenda, {})[0], [])
        self.state['projects'] = {'table':{'status':'in_progress','location':'workshop','title':'Table ordered',
                                        'references':['demo:initial_configuration'],'audience':['narrator','player']}}
        self.assertEqual(project(self.state, {}, {})[0][0]['type'], 'project')
        self.state['projects']['table']['status'] = 'finished'
        self.assertEqual(project(self.state, {}, {})[0], [])

    def test_private_commitment_is_not_exposed(self):
        self.state['commitments']['plans']['audience'] = ['narrator']
        self.assertTrue(project(self.state, {}, {})[0])
        self.assertEqual(project(self.state, {}, {}, recipient='player')[0], [])

    def test_schema_accepts_old_and_optional_location_without_migration(self):
        check('state', self.state)
        self.state['commitments']['plans'].pop('reminder_location')
        check('state', self.state)
        self.state['commitments']['plans']['reminder_location'] = ''
        with self.assertRaises(ValueError): check('state', self.state)


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'.tmp-tests').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp-tests')
        state = initial_state('integration_reminder')
        state['positions']['player'] = 'workshop'
        state['commitments'] = {'plans':agreement()}
        self.c = Campaign(Path(self.temp.name)/'novel_engine.sqlite').create(state, configuration())
        self.c.open_scene('The craftsman sorts his papers.')
        self.n = FakeNarrator()
        self.loop = NarrativeLoop(self.c, self.n)

    def tearDown(self):
        self.temp.cleanup()

    def packet(self):
        prepared = self.c.prepare_turn(narrative_memory=True)
        prompt = self.loop.build_prompt(prepared)
        return json.loads(prompt.rsplit('\nCURRENT TURN\n',1)[1])

    def session(self):
        with self.c.transaction() as con:
            return self.c._session(con)

    def move(self, destination):
        self.n.response = {'narration':'The journey ends.', 'blocking_questions':[],
            'events':[{'world_minute':self.c.state()['world_minute'], 'description':'The player arrives.',
                       'audience':['narrator','player'], 'causes':['current_action'], 'conditions':[],
                       'changes':[{'operation':'set','path':'/positions/player','value_json':json.dumps(destination)}]}]}
        return self.loop.run_turn('I go toward '+destination+'.')

    def test_repeated_preparation_does_not_consume_or_resolve_reminder(self):
        self.c.begin_action('Hello.')
        before = self.c.resume()['session']
        state = self.c.state()
        for _ in range(2):
            packet = self.packet()
            self.assertTrue(packet['place_reminders'])
            self.assertEqual(set(packet['allowed_references']), packet_references(packet))
        self.assertEqual(self.c.resume()['session'], before)
        self.assertEqual(self.c.state(), state)
        self.assertEqual(self.n.calls, 0)

    def test_presented_once_then_reactivated_after_departure_and_return(self):
        self.assertEqual(self.loop.run_turn('Hello.')['status'], 'recorded')
        self.c.begin_action('I look at the papers.')
        self.assertNotIn('place_reminders', self.packet())
        self.loop.run_turn(resume=True)
        self.move('place')
        self.move('workshop')
        self.c.begin_action('I greet the craftsman.')
        self.assertTrue(self.packet()['place_reminders'])
        self.assertEqual(self.c.state()['commitments']['plans']['status'], 'open')

    def test_question_or_failed_generation_does_not_consume(self):
        self.n.response = {'narration':'Specify your request.', 'events':[], 'blocking_questions':['Quels plans ?']}
        self.assertEqual(self.loop.run_turn('Hello.')['status'], 'question')
        self.c.begin_action('The plans of the table.')
        self.assertTrue(self.packet()['place_reminders'])
        with patch.object(self.n, 'generate', side_effect=ModelUnavailable('Interruption fictional')):
            with self.assertRaises(ModelUnavailable): self.loop.run_turn(resume=True)
        self.assertTrue(self.packet()['place_reminders'])

    def test_rejected_action_does_not_consume(self):
        self.n.response = {'narration':'An attempt.', 'blocking_questions':[],
            'events':[{'world_minute':self.c.state()['world_minute'], 'description':'Impossible attempt.',
                       'audience':['narrator','player'], 'causes':['current_action'],
                       'conditions':[{'path':'/positions/player','equals_json':'"place"'}], 'changes':[]}]}
        self.assertEqual(self.loop.run_turn('Hello.')['status'], 'rejected')
        self.assertNotIn('place_reminders', self.session())
        self.assertTrue(self.c.prepare_turn(narrative_memory=True)['place_reminders'])

    def test_prepared_receipt_survives_crash_and_is_idempotent(self):
        with patch.object(self.c, 'apply', side_effect=RuntimeError('Crash before transaction')):
            with self.assertRaises(RuntimeError): self.loop.run_turn('Hello.')
        self.assertNotIn('place_reminders', self.session())
        restored = NarrativeLoop(self.c, self.n)
        self.assertEqual(restored.run_turn(resume=True)['status'], 'recorded')
        self.assertEqual(self.n.calls, 1)
        marked = deepcopy(self.session()['place_reminders'])
        self.assertNotIn('place_reminders', self.c.resume()['session'])
        restored.run_turn(resume=True)
        self.assertEqual(self.session()['place_reminders'], marked)
        self.assertTrue(marked['seen'])

    def test_omitted_reminder_is_not_consumed(self):
        self.c.begin_action('Hello.')
        packet = self.packet()
        size = len(self.loop.build_prompt(self.c.prepare_turn(narrative_memory=True)).encode())
        self.loop.context_budget_bytes = size - 200
        result = self.loop.run_turn(resume=True)
        self.assertEqual(result['status'], 'recorded')
        self.assertNotIn('place_reminders', self.session())
        diag = self.loop.last_metrics['context_diagnostic']['place_reminders']['reminders']
        self.assertFalse(diag[0]['sent'])
        self.assertEqual(diag[0]['final_reason'], 'prompt_budget')
        self.assertTrue(self.c.prepare_turn(narrative_memory=True)['place_reminders'])

    def test_late_transaction_failure_rolls_back_presentation(self):
        from novel_engine.creations import CreationRegistry
        before = self.c.state()
        with patch.object(CreationRegistry, 'apply', side_effect=ValueError('Fictional registry failure')):
            with self.assertRaises(ValueError): self.loop.run_turn('Hello.')
        self.assertEqual(self.c.state(), before)
        self.assertNotIn('place_reminders', self.session())
        self.assertEqual(NarrativeLoop(self.c,self.n).run_turn(resume=True)['status'], 'recorded')
        self.assertTrue(self.session()['place_reminders']['seen'])


if __name__ == '__main__': unittest.main()
