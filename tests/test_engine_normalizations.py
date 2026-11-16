"""Repairs made before validation: declarative public consequences, commitment objects, escaped narration."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from novel_engine.campaign import Campaign

from novel_engine.consequences import normalize_commitments, reconcile_public
from novel_engine.demo import configuration, initial_state, proposal


def claim(kind, path, value):
    return {'kind': kind, 'path': path, 'value_json': json.dumps(value), 'source_reference': 'x'}


class ReconcilePublicTests(unittest.TestCase):
    after = {'commitments': {'quest': {'title': 'T', 'status': 'open'}}, 'knowledge': {}, 'resources': {'player_hp': 70}}

    def run_claims(self, claims):
        out = reconcile_public(self.after, [{'event_id': 'e', 'audience': ['player'], 'public_consequences': claims}])
        return out[0]['public_consequences']

    def test_announced_but_unwritten_declarative_claim_is_dropped(self):
        self.assertEqual(self.run_claims([claim('knowledge', '/knowledge/accepted', True)]), [])

    def test_differing_commitment_takes_the_final_state(self):
        result = self.run_claims([claim('commitment', '/commitments/quest', {'title': 'T'})])
        self.assertEqual(json.loads(result[0]['value_json']), {'title': 'T', 'status': 'open'})

    def test_computable_claims_are_untouched(self):
        result = self.run_claims([claim('damage', '/resources/player_hp', 99)])
        self.assertEqual(json.loads(result[0]['value_json']), 99)

    def test_input_is_not_mutated(self):
        events = [{'event_id': 'e', 'audience': ['player'],
                   'public_consequences': [claim('knowledge', '/knowledge/accepted', True)]}]
        reconcile_public(self.after, events)
        self.assertEqual(len(events[0]['public_consequences']), 1)


class NormalizeCommitmentsTests(unittest.TestCase):
    def make(self, value, operation='set', path='/commitments/escort'):
        return {'event_id': 'e', 'audience': ['narrator', 'player'], 'causes': ['action:r1'],
                'changes': [{'operation': operation, 'path': path, 'value': value}]}

    def test_free_form_object_is_brought_to_the_schema(self):
        out = normalize_commitments(self.make({'title': 'Escort', 'status': 'accepted', 'wagons': 0, 'deadline_minute': 9}))
        self.assertEqual(out['changes'][0]['value'], {'description': 'Escort', 'status': 'open',
                                                      'references': ['action:r1'], 'audience': ['narrator', 'player']})

    def test_valid_object_is_kept(self):
        good = {'description': 'Keep the date', 'status': 'kept', 'references': ['r'], 'audience': ['player']}
        self.assertEqual(normalize_commitments(self.make(dict(good)))['changes'][0]['value'], good)

    def test_other_changes_are_not_touched(self):
        original = self.make(True, path='/knowledge/player')
        self.assertIs(normalize_commitments(original), original)
        deletion = self.make(None, operation='delete')
        self.assertIs(normalize_commitments(deletion), deletion)

    def test_missing_description_falls_back_on_the_key(self):
        out = normalize_commitments(self.make({'status': 'completed'}, path='/commitments/harvest_fair_signup'))
        self.assertEqual(out['changes'][0]['value']['description'], 'harvest fair signup')
        self.assertEqual(out['changes'][0]['value']['status'], 'kept')


class EscapedNarrationTests(unittest.TestCase):
    def test_escaped_quotes_and_line_breaks_are_recorded_as_prose(self):
        (ROOT / '.tmp-tests').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp-tests') as folder:
            campaign = Campaign(Path(folder) / 'campaign.sqlite').create(initial_state(), configuration())
            narration = 'The guard looks up.' + r'\n\n\"Halt,\" he says.'
            result = campaign.apply(proposal(campaign.state(), narration))
            self.assertEqual(result['status'], 'recorded', result)
            self.assertEqual(result['narration'], 'The guard looks up.\n\n"Halt," he says.')


if __name__ == '__main__':
    unittest.main()
