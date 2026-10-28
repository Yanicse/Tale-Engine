"""Updating a planned instruction keeps its date and its engine revision."""
from copy import deepcopy
import unittest

from novel_engine.agenda_protocol import materialize, metadata_redefinitions, retry_metadata_correction


class AgendaCommandTests(unittest.TestCase):
    def setUp(self):
        self.entry = {'entry_id': 'followup', 'revision': 3, 'title': 'Prepare the follow-up',
                      'actor': 'craftsman', 'location': 'workshop', 'kind': 'project',
                      'conditions': [], 'audience': ['narrator', 'player'],
                      'interrupt': False, 'due_minute': 300}
        self.packet = {'events': [self.entry]}
        self.command = {'entry_id': 'followup', 'status': 'planned', 'reason': 'New instruction accepted.',
                        'references': ['current_action'], 'outcome_event_index': None,
                        'due_minute': None, 'creation': None}

    def test_instruction_update_preserves_date_revision_and_metadata_without_mutation(self):
        packet, command = deepcopy(self.packet), deepcopy(self.command)
        update = materialize([self.command], self.packet)[0]
        self.assertEqual(update['due_minute'], 300)
        self.assertEqual(update['expected_revision'], 3)
        self.assertEqual(update['reason'], self.command['reason'])
        self.assertEqual(update['status'], 'planned')
        self.assertEqual(update['audience'], self.entry['audience'])
        self.assertEqual(self.packet, packet)
        self.assertEqual(self.command, command)

    def test_numeric_date_requires_explicit_report_even_if_identical(self):
        for date in (300, 400):
            self.command['due_minute'] = date
            with self.assertRaisesRegex(ValueError, 'use null outside a postponement'):
                materialize([self.command], self.packet)
        self.command['status'] = 'postponed'
        self.assertEqual(materialize([self.command], self.packet)[0]['due_minute'], 400)

    def test_existing_metadata_rejected_even_if_repeated_identically(self):
        for title in (self.entry['title'], 'Prepare the next step'):
            command = deepcopy(self.command)
            command['creation'] = {key: deepcopy(self.entry[key]) for key in
                                   ('title', 'actor', 'location', 'kind', 'conditions', 'audience', 'interrupt')}
            command['creation']['title'] = title
            before = deepcopy(command)
            with self.assertRaisesRegex(ValueError, 'two distinct commands'):
                materialize([command], self.packet)
            self.assertEqual(command, before)

    def test_retry_diagnostic_lists_all_offenders_without_mutating_receipt(self):
        import json
        bad = {**deepcopy(self.command), 'creation': {'title': 'Suite'}}
        commands = [bad, self.command, {**bad, 'entry_id': 'new_step'}, deepcopy(bad)]
        self.assertEqual(metadata_redefinitions(commands, self.packet), [
            {'index': 0, 'entry_id': 'followup'}, {'index': 3, 'entry_id': 'followup'}])
        receipt = {'received': json.dumps({'agenda_updates': commands}), 'packet': {'world_agenda': self.packet}}
        before = deepcopy(receipt)
        diagnostic = retry_metadata_correction(receipt)
        self.assertEqual(diagnostic['commands_to_fix'], metadata_redefinitions(commands, self.packet))
        self.assertEqual(receipt, before)
        self.assertIsNone(retry_metadata_correction({'received': 'not JSON'}))
        self.assertIsNone(retry_metadata_correction({'received': {'agenda_updates': [self.command]}}))

    def test_separate_successor_keeps_original_metadata(self):
        resolution = {**self.command, 'status': 'done', 'outcome_event_index': 0}
        creation = {key: deepcopy(self.entry[key]) for key in
                    ('title', 'actor', 'location', 'kind', 'conditions', 'audience', 'interrupt')}
        creation['title'] = 'Next step of the same actor'
        successor = {**self.command, 'entry_id': 'followup:next_step', 'creation': creation,
                     'due_minute': 400, 'references': ['agenda:followup']}
        old, new = materialize([resolution, successor], self.packet)
        self.assertEqual(old['title'], self.entry['title'])
        self.assertEqual(old['expected_revision'], 3)
        self.assertEqual(new['title'], creation['title'])
        self.assertEqual(new['expected_revision'], -1)
