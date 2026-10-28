"""Configured charge phases are enforced in persisted, atomic turns."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from novel_engine.campaign import Campaign
from novel_engine.demo import configuration, initial_state, event, proposal
from novel_engine.pack import campaign_config, seed_resources
from novel_engine.pack_check import check_pack


class ChargePhasesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='.tmp-charge-phases-', dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def create(self, charge, schedule=True):
        rules = {'abilities': {'spark': {'free': True},
                              'shield': {'free': True, 'effect': 'death_prevention', 'parameters': {
                                  'duration_seconds': 60, 'lethal_prevention_capacity_hp_max_percent': 100,
                                  'survival_hp_max_percent': 10}}}, 'charges': {'player_charges': charge},
                 'resurrection_hp_percent': 50}
        if schedule:
            rules['solar_schedule'] = {'first_sunrise_minute': 360, 'first_sunset_minute': 1080}
        sheet = {'name': 'Ana', 'hp_max': 50, 'mana_max': 30, 'rules': rules}
        (self.root / 'novel.json').write_text('{"title":"Test"}', encoding='utf-8')
        (self.root / 'character.json').write_text(json.dumps(sheet), encoding='utf-8')
        self.assertEqual(check_pack(self.root).errors, [])
        config = campaign_config(self.root, configuration())
        state = initial_state('phases')
        state['resources'] = seed_resources(sheet, config)
        return Campaign(self.root / 'campaign.sqlite').create(state, config)

    def apply(self, c, kinds, minute):
        operations = [{'kind': kind, 'at_second': minute * 60,
                       'ability_id': ('shield' if 'prevent_death' in kinds else 'spark') if kind == 'ability' else None,
                       'output_percent': None,
                       'amount': (50 if 'resurrection' in kinds else 10) if kind == 'damage' else (50 if kind == 'prevent_death' else None),
                       'start_second': None, 'source_reference': 'current_action'} for kind in kinds]
        e = event(c.state(), 'The action completes.', [], minute=minute, causes=['current_action'])
        e['mechanical_operations'] = operations
        p = proposal(c.state(), 'Done.', [e])
        return c.apply(p), p

    def test_always_resurrection_needs_no_solar_schedule_and_replays_once(self):
        c = self.create({'period': 'combat', 'consumed_by': 'resurrection', 'phase': 'always'}, schedule=False)
        result, p = self.apply(c, ['damage', 'resurrection'], 0)
        self.assertEqual(result['status'], 'recorded', result)
        after = c.state()
        self.assertEqual((after['resources']['player_hp'], after['resources']['player_charges']), (25, 0))
        self.assertEqual(Campaign(c.path).apply(p), result)
        self.assertEqual(c.state(), after)

    def test_ability_charge_phase_rejects_and_rolls_back_prior_damage(self):
        c = self.create({'period': 'combat', 'ability': 'spark', 'phase': 'night'})
        before = deepcopy(c.state())
        result, _ = self.apply(c, ['damage', 'ability'], 400)
        self.assertEqual(result['status'], 'rejected', result)
        self.assertIn('form', result['reason'])
        self.assertEqual(c.state(), before)
        result, _ = self.apply(c, ['ability'], 1100)
        self.assertEqual(result['status'], 'recorded', result)
        self.assertEqual(c.state()['resources']['player_charges'], 0)

    def test_resurrection_wrong_phase_preserves_the_entire_turn(self):
        c = self.create({'period': 'combat', 'consumed_by': 'resurrection', 'phase': 'night'})
        before = c.state()
        result, _ = self.apply(c, ['damage', 'resurrection'], 400)
        self.assertEqual(result['status'], 'rejected', result)
        self.assertEqual(c.state(), before)

    def test_death_prevention_charge_obeys_its_phase(self):
        c = self.create({'period': 'combat', 'consumed_by': 'prevent_death', 'phase': 'day'})
        before = c.state()
        result, _ = self.apply(c, ['combat_start', 'ability', 'damage', 'prevent_death'], 0)
        self.assertEqual(result['status'], 'rejected', result)
        self.assertIn('form', result['reason'])
        self.assertEqual(c.state(), before)
