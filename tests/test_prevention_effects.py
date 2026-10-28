"""Death prevention retains the active ability's scaled, persisted parameters."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from novel_engine.campaign import Campaign
from novel_engine.demo import configuration, initial_state, event, proposal
from novel_engine.pack import campaign_config, seed_resources
from novel_engine.pack_check import check_pack


class PreventionEffectsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='.tmp-prevention-', dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        def ward(survival, duration=60, scaling=None):
            return {'free': True, 'effect': 'death_prevention', 'parameters': {
                'duration_seconds': duration, 'lethal_prevention_capacity_hp_max_percent': 100,
                'survival_hp_max_percent': survival}, 'scaling': scaling or {}}
        sheet = {'name': 'Ana', 'hp_max': 100, 'mana_max': 0, 'rules': {
            'abilities': {'small_ward': ward(10),
                          'large_ward': ward(80, scaling={'survival_hp_max_percent': 'linear'}),
                          'brief_ward': ward(10, duration=5, scaling={'duration_seconds': 'linear'})},
            'charges': {'player_wards': {'period': 'combat', 'consumed_by': 'prevent_death'}}}}
        (self.root / 'novel.json').write_text('{"title":"Test"}', encoding='utf-8')
        (self.root / 'character.json').write_text(json.dumps(sheet), encoding='utf-8')
        self.assertEqual(check_pack(self.root).errors, [])
        config = campaign_config(self.root, configuration())
        state = initial_state('prevention')
        state['resources'] = seed_resources(sheet, config)
        self.c = Campaign(self.root / 'campaign.sqlite').create(state, config)

    def op(self, kind, ability=None, output=None, amount=None, at=0):
        return {'kind': kind, 'at_second': at, 'ability_id': ability, 'output_percent': output,
                'amount': amount, 'start_second': None, 'source_reference': 'current_action'}

    def apply(self, operations, second=0):
        e = event(self.c.state(), 'The action completes.', [], causes=['current_action'])
        e.update(world_second=second, mechanical_operations=operations)
        p = proposal(self.c.state(), 'Done.', [e])
        return self.c.apply(p), p

    def activate(self, ability, output):
        result, _ = self.apply([self.op('combat_start'), self.op('ability', ability, output)])
        self.assertEqual(result['status'], 'recorded', result)

    def test_active_ability_survival_is_persisted_and_scaled(self):
        self.activate('large_ward', 10)
        self.c = Campaign(self.c.path)
        result, p = self.apply([self.op('prevent_death', amount=100)])
        self.assertEqual(result['status'], 'recorded', result)
        self.assertEqual(self.c.state()['resources']['player_hp'], 8)
        after = deepcopy(self.c.state())
        self.assertEqual(self.c.apply(p), result)
        self.assertEqual(self.c.state(), after)
        result, _ = self.apply([self.op('prevent_death', amount=100)])
        self.assertEqual(result['status'], 'rejected', result)
        self.assertEqual(self.c.state(), after)

    def test_fractional_duration_is_rounded_up_to_a_fictional_second(self):
        self.activate('brief_ward', 10)
        self.assertEqual(self.c.state()['resources']['player_prevention_until_second'], 1)
        before = self.c.state()
        result, _ = self.apply([self.op('prevent_death', amount=100, at=1)], second=1)
        self.assertEqual(result['status'], 'rejected', result)
        self.assertEqual(self.c.state(), before)

    def test_latest_activation_replaces_the_previous_survival_value(self):
        self.activate('large_ward', 100)
        result, _ = self.apply([self.op('ability', 'small_ward'), self.op('prevent_death', amount=100)])
        self.assertEqual(result['status'], 'recorded', result)
        self.assertEqual(self.c.state()['resources']['player_hp'], 10)

    def test_active_survival_value_cannot_be_forged(self):
        self.activate('large_ward', 10)
        before = self.c.state()
        e = event(before, 'The action completes.', [{'operation': 'set',
                  'path': '/resources/player_prevention_survival_hp', 'value': 100}])
        result = self.c.apply(proposal(before, 'Done.', [e]))
        self.assertEqual(result['status'], 'rejected', result)
        self.assertEqual(self.c.state(), before)

    def test_reserved_survival_resource_cannot_be_a_currency(self):
        (self.root / 'novel.json').write_text(json.dumps({'title': 'Test', 'currencies': {
            'prevention_survival_hp': {'initial_balance': 100}}}), encoding='utf-8')
        self.assertTrue(any('collides' in error for error in check_pack(self.root).errors))
