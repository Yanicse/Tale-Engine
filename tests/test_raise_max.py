"""raise_max: maximum HP and mana grow through a validated operation only."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from novel_engine.campaign import Campaign
from novel_engine.character import validate_character_transition
from novel_engine.demo import configuration, initial_state, event, proposal
from novel_engine.pack import campaign_config, seed_resources, seed_powers


class RaiseMaxTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='.tmp-raise-', dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        sheet = {'name': 'Ana', 'hp_max': 100, 'mana_max': 10}
        (root / 'novel.json').write_text('{"title":"Test"}', encoding='utf-8')
        (root / 'character.json').write_text(json.dumps(sheet), encoding='utf-8')
        config = campaign_config(root, configuration())
        state = initial_state('raise')
        state['resources'] = seed_resources(sheet, config)
        state['powers'] = seed_powers(sheet)
        self.c = Campaign(root / 'campaign.sqlite').create(state, config)

    def operation(self, kind, ability=None, amount=None):
        return {'kind': kind, 'at_second': self.c.state()['world_minute'] * 60, 'ability_id': ability,
                'output_percent': None, 'amount': amount, 'start_second': None, 'source_reference': 'current_action'}

    def apply(self, operations, changes=()):
        state = self.c.state()
        e = event(state, 'The action completes.', list(changes), causes=['current_action'])
        e['mechanical_operations'] = list(operations)
        return self.c.apply(proposal(state, 'Done.', [e]))

    def test_raise_both_maxima_without_healing(self):
        self.assertEqual(self.apply([self.operation('damage', amount=40)])['status'], 'recorded')
        result = self.apply([self.operation('raise_max', 'hp', 205), self.operation('raise_max', 'mana', 22)])
        self.assertEqual(result['status'], 'recorded', result)
        resources = self.c.state()['resources']
        self.assertEqual((resources['player_hp'], resources['player_hp_max']), (60, 205))
        self.assertEqual((resources['player_mana'], resources['player_mana_max']), (10, 22))

    def test_a_raised_maximum_caps_later_recovery_and_stays_after_a_second_turn(self):
        self.apply([self.operation('damage', amount=90)])
        self.apply([self.operation('raise_max', 'hp', 150)])
        self.assertEqual(self.apply([self.operation('heal', amount=500)])['status'], 'recorded')
        self.assertEqual(self.c.state()['resources']['player_hp'], 150)
        self.assertEqual(self.apply([self.operation('damage', amount=1)])['status'], 'recorded')
        self.assertEqual(self.c.state()['resources']['player_hp_max'], 150)

    def test_the_maximum_only_rises_and_says_why(self):
        before = self.c.state()
        cases = (([self.operation('raise_max', 'hp', 100)], 'above the current maximum'),
                 ([self.operation('raise_max', 'hp', 50)], 'above the current maximum'),
                 ([self.operation('raise_max', 'mana', None)], 'above the current maximum'),
                 ([self.operation('raise_max', 'luck', 500)], "'hp' or 'mana'"),
                 ([self.operation('raise_max', None, 500)], "'hp' or 'mana'"))
        for operations, reason in cases:
            result = self.apply(operations)
            self.assertEqual(result['status'], 'rejected', operations)
            self.assertIn(reason, result['reason'])
        self.assertEqual(self.c.state(), before)

    def test_a_free_change_of_a_maximum_is_refused_with_mechanics_installed(self):
        before = self.c.state()
        for key in ('player_hp_max', 'player_mana_max'):
            result = self.apply([], [{'operation': 'set', 'path': '/resources/' + key, 'value': 500}])
            self.assertEqual(result['status'], 'rejected')
            self.assertIn('Free mechanical mutation forbidden', result['reason'])
        self.assertEqual(self.c.state(), before)

    def test_no_growth_after_a_death(self):
        self.assertEqual(self.apply([self.operation('damage', amount=100)])['status'], 'recorded')
        result = self.apply([self.operation('raise_max', 'hp', 300)])
        self.assertEqual(result['status'], 'rejected')
        self.assertIn('Growth after a death', result['reason'])

    def test_without_the_mechanics_a_maximum_still_cannot_move_at_all(self):
        config = {'character_rules': {'hp_max': 100, 'mana_max': 10}}
        before = {'world_minute': 0, 'resources': {'player_hp': 100, 'player_hp_max': 100, 'player_mana': 10,
                                                   'player_mana_max': 10}}
        validate_character_transition(before, deepcopy(before), config)
        for key, value in (('player_hp_max', 150), ('player_mana_max', 20)):
            after = deepcopy(before)
            after['resources'][key] = value
            with self.assertRaisesRegex(ValueError, 'cannot change'):
                validate_character_transition(before, after, config)

    def test_with_the_mechanics_a_maximum_never_goes_down(self):
        config = {'character_rules': {'hp_max': 100, 'mana_max': 10}, 'mechanics': {'version': 'mana-1.0'}}
        before = {'world_minute': 0, 'resources': {'player_hp': 100, 'player_hp_max': 150, 'player_mana': 10,
                                                   'player_mana_max': 22}}
        validate_character_transition(before, deepcopy(before), config)
        for key, value in (('player_hp_max', 140), ('player_mana_max', 21)):
            after = deepcopy(before)
            after['resources'][key] = value
            after['resources']['player_hp'] = min(after['resources']['player_hp'], after['resources']['player_hp_max'])
            with self.assertRaisesRegex(ValueError, 'cannot decrease'):
                validate_character_transition(before, after, config)


if __name__ == '__main__':  # run: python -m unittest tests.test_raise_max
    unittest.main()
