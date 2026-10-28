"""Mana is independent from health and is spent transactionally in pack campaigns."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from novel_engine.campaign import Campaign
from novel_engine.demo import configuration, initial_state, event, proposal
from novel_engine.pack import campaign_config, seed_resources, seed_powers
from novel_engine.pack_check import check_pack
from novel_engine.reader import Reader


class ManaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='.tmp-mana-', dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sheet = {'name': 'Ana', 'hp_max': 50, 'mana_max': 30, 'rules': {
            'abilities': {'gale': {'label': 'Gale', 'cooldown': {'resource': 'player_gale_ready', 'seconds': 60}},
                          'spark': {'label': 'Spark', 'free': True}},
            'costs': {'full_power_cost_percent': {'gale': 30}}}}
        self.save = self.root / 'campaign.sqlite'
        self.c = self.create()

    def create(self, sheet=None):
        sheet = sheet or self.sheet
        (self.root / 'novel.json').write_text('{"title":"Test"}', encoding='utf-8')
        (self.root / 'character.json').write_text(json.dumps(sheet), encoding='utf-8')
        self.assertEqual(check_pack(self.root).errors, [])
        config = campaign_config(self.root, configuration())
        state = initial_state('mana')
        state['resources'] = seed_resources(sheet, config)
        state['powers'] = seed_powers(sheet)
        return Campaign(self.save).create(state, config)

    def operation(self, kind='ability', ability='gale', amount=None, at=None):
        return {'kind': kind, 'at_second': self.c.state()['world_minute'] * 60 if at is None else at,
                'ability_id': ability if kind == 'ability' else None, 'output_percent': None,
                'amount': amount, 'start_second': None, 'source_reference': 'current_action'}

    def apply(self, operations=(), narration='Done.', changes=(), minute=None):
        state = self.c.state()
        e = event(state, 'The action completes.', list(changes), minute=minute, causes=['current_action'])
        e['mechanical_operations'] = list(operations)
        p = proposal(state, narration, [e])
        return self.c.apply(p), p

    def test_cast_spends_mana_once_and_keeps_health(self):
        result, p = self.apply([self.operation()], 'You cast Gale.')
        self.assertEqual(result['status'], 'recorded', result)
        state = self.c.state()
        self.assertEqual((state['resources']['player_hp'], state['resources']['player_mana']), (50, 21))
        self.assertEqual(Campaign(self.save).apply(p), result)
        self.assertEqual(self.c.state(), state)
        skills = Reader(self.save).abilities()
        self.assertEqual((skills['mana'], skills['mana_max']), (21, 30))
        gale = next(a for a in skills['abilities'] if a['id'] == 'gale')
        self.assertEqual(gale['default_mana_cost'], 9)
        self.assertEqual(gale['cooldown']['seconds_left'], 60)

    def test_cast_without_operation_and_direct_resource_edits_are_rejected(self):
        before = self.c.state()
        self.assertEqual(self.apply(narration='You cast Gale.')[0]['status'], 'rejected')
        for key in ('player_hp', 'player_mana', 'player_mana_max'):
            result, _ = self.apply(changes=[{'operation': 'set', 'path': '/resources/' + key, 'value': 1}])
            self.assertEqual(result['status'], 'rejected', result)
        self.assertEqual(self.c.state(), before)

        self.save = self.root / 'no_magic.sqlite'
        self.c = self.create({'name': 'Ana', 'hp_max': 10, 'mana_max': 0})
        result, _ = self.apply([self.operation()])
        self.assertEqual(result['status'], 'rejected', result)
        self.assertIn('Unknown ability', result['reason'])

    def test_exact_last_mana_is_spendable_and_insufficient_mana_rolls_back_damage(self):
        self.sheet['mana_max'] = 1
        self.sheet['rules']['costs']['full_power_cost_percent']['gale'] = 100
        self.sheet['rules']['abilities']['gale'].pop('cooldown')
        self.save = self.root / 'one.sqlite'
        self.c = self.create()
        self.assertEqual(self.apply([self.operation()])[0]['status'], 'recorded')
        self.assertEqual(self.c.state()['resources']['player_mana'], 0)
        before = self.c.state()
        result, _ = self.apply([self.operation('damage', amount=10), self.operation()])
        self.assertEqual(result['status'], 'rejected')
        self.assertIn('Insufficient mana', result['reason'])
        self.assertEqual(self.c.state(), before)
        self.assertEqual(self.apply([self.operation(ability='spark')])[0]['status'], 'recorded')

    def test_elapsed_time_does_not_recover_health_or_mana_and_explicit_recovery_is_capped(self):
        self.apply([self.operation(), self.operation('damage', amount=10)])
        self.assertEqual(self.apply(minute=3000)[0]['status'], 'recorded')
        self.assertEqual((self.c.state()['resources']['player_hp'], self.c.state()['resources']['player_mana']), (40, 21))
        self.assertEqual(self.apply([self.operation('restore_mana', amount=100)])[0]['status'], 'recorded')
        self.assertEqual(self.c.state()['resources']['player_mana'], 30)
        self.assertEqual(self.c.state()['resources']['player_hp'], 40)

    def test_cooldown_cannot_be_reset_by_reopening(self):
        self.apply([self.operation()])
        self.c = Campaign(self.save)
        before = self.c.state()
        self.assertEqual(self.apply([self.operation()])[0]['status'], 'rejected')
        self.assertEqual(self.c.state(), before)
        result, _ = self.apply([self.operation(at=60)], minute=1)
        self.assertEqual(result['status'], 'recorded', result)

    def test_night_and_combat_charges_can_be_configured_in_a_pack(self):
        sheet = deepcopy(self.sheet)
        sheet['rules']['solar_schedule'] = {'first_sunrise_minute': 360, 'first_sunset_minute': 1080}
        sheet['rules']['charges'] = {'player_sparks': {'period': 'night', 'ability': 'spark'},
                                     'player_gales': {'period': 'combat', 'ability': 'gale'}}
        self.save = self.root / 'charges.sqlite'; self.c = self.create(sheet)
        result, _ = self.apply([self.operation('combat_start'), self.operation(), self.operation(ability='spark')])
        self.assertEqual(result['status'], 'recorded', result)
        self.assertEqual(self.c.state()['resources']['player_sparks'], 0)
        self.apply(minute=1080)
        self.assertEqual(self.c.state()['resources']['player_sparks'], 1)
        self.assertEqual(self.c.state()['resources']['player_mana'], 21)

    def test_removed_regeneration_and_colliding_resources_are_invalid(self):
        for rules in ({'regeneration': {}}, {'hunger_from_health': True},
                      {'charges': {'player_mana': {'period': 'combat', 'ability': 'gale'}}},
                      {'abilities': {'gale': {'cooldown': {'resource': 'player_hp', 'seconds': 1}}}}):
            sheet = deepcopy(self.sheet); sheet['rules'].update(rules)
            (self.root / 'character.json').write_text(json.dumps(sheet), encoding='utf-8')
            self.assertTrue(check_pack(self.root).errors)

    def test_currency_cannot_overwrite_an_ability_cooldown(self):
        (self.root / 'novel.json').write_text('{"title":"Test","currencies":{"gale_ready":{}}}', encoding='utf-8')
        self.assertTrue(any('collides' in error for error in check_pack(self.root).errors))
