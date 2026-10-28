"""Charges, cooldowns and death prevention driven by a fictional configuration."""
import os
import unittest
from copy import deepcopy
from unittest import mock

from novel_engine import abilities, targeted_context, cooldowns
from novel_engine.mechanics import materialize
from novel_engine.character import validate_character_transition

RULES = {
    'version': 'fictional-1', 'hp_max': 100, 'hunger_from_health': True, 'forbidden_resources': ['player_hunger'],
    'solar_schedule': {'first_sunrise_minute': 360, 'first_sunset_minute': 1080},
    'regeneration': {'day': {'percent': 1, 'seconds': 3600}, 'night': {'percent': 2, 'seconds': 3600},
                     'drunk_blood': {'percent': 5, 'seconds': 60}},
    'passives': {'lantern': {'label': 'Watch lantern', 'phase': 'always', 'free': True}},
    'abilities': {
        'dawn_shield': {'phase': 'always', 'effect': 'death_prevention',
                          'parameters': {'duration_seconds': 60, 'lethal_prevention_capacity_hp_max_percent': 50,
                                         'survival_hp_max_percent': 1}},
        'bolt': {'phase': 'always', 'label': 'Lightning', 'catalogue': {'offensive': True, 'aliases': ['lightning']},
                   'cooldown': {'resource': 'player_lightning_available_at_second', 'seconds': 3600}},
        'storm': {'phase': 'night'},
    },
    'charges': {
        'player_storms_left': {'period': 'night', 'ability': 'storm'},
        'player_resurrections_left': {'period': 'night', 'consumed_by': 'resurrection', 'phase': 'night'},
        'player_death_refusal_available': {'period': 'combat', 'ability': 'dawn_shield', 'consumed_by': 'prevent_death'},
    },
}
CONFIG = {'player_name': 'Mira', 'mechanics': {'version': 'vitality-1.0'}, 'character_rules': RULES,
          'spell_cost_rules': {'default_output_percent': 10,
                               'full_power_cost_percent': {'dawn_shield': 0, 'bolt': 20, 'storm': 0}}}
RESOURCES = {'player_hp': 100, 'player_hp_max': 100, 'night_id': 0, 'player_combat_id': 0, 'player_combat_active': 0,
             'player_storms_left': 0, 'player_resurrections_left': 0, 'player_death_refusal_available': 0,
             'player_lightning_available_at_second': 0}


def state(minute=1000, **resources):
    return {'world_minute': minute, 'resources': {**RESOURCES, **resources}, 'powers': {}}


def op(kind, at, ability=None, amount=None, output=None):
    return {'kind': kind, 'at_second': at, 'ability_id': ability, 'output_percent': output,
            'amount': amount, 'start_second': None, 'source_reference': 'current_action'}


def run(before, minute, operations, changes=()):
    event = {'world_minute': minute, 'changes': list(changes), 'causes': ['current_action'],
             'mechanical_operations': operations}
    result = materialize(before, event, CONFIG)
    resources = deepcopy(before['resources'])
    for change in result['changes']:
        resources[change['path'].split('/')[-1]] = change['value']
    return resources


class GenericMechanicsTest(unittest.TestCase):
    def test_night_charges_return_only_at_sunset(self):
        after = run(state(), 1070, [])
        self.assertEqual(after['player_storms_left'], 0)
        after = run(state(), 1100, [])
        self.assertEqual((after['night_id'], after['player_storms_left'], after['player_resurrections_left']),
                         (1, 1, 1))
        self.assertEqual(after['player_death_refusal_available'], 0)

    def test_forbidden_resource_is_rejected(self):
        from novel_engine.character import validate_character_transition
        before, after = state(), state(player_hunger=1)
        with self.assertRaisesRegex(ValueError, 'forbidden'):
            validate_character_transition(before, after, {'character_rules': RULES})

    def test_combat_start_restores_combat_charges(self):
        after = run(state(), 1000, [op('combat_start', 60000)])
        self.assertEqual((after['player_combat_id'], after['player_combat_active'], after['player_death_refusal_available']),
                         (1, 1, 1))

    def test_ability_charge_and_form(self):
        with self.assertRaisesRegex(ValueError, 'form'):
            run(state(player_storms_left=1), 1000, [op('ability', 60000, 'storm')])
        night = state(1100, player_storms_left=1, night_id=1)
        self.assertEqual(run(night, 1100, [op('ability', 66000, 'storm')])['player_storms_left'], 0)
        with self.assertRaisesRegex(ValueError, 'Charge unavailable'):
            run(state(1100, night_id=1), 1100, [op('ability', 66000, 'storm')])

    def test_cooldown_from_configuration(self):
        after = run(state(), 1000, [op('ability', 60000, 'bolt')])
        self.assertEqual(after['player_lightning_available_at_second'], 63600)
        self.assertEqual(after['player_hp'], 98)
        with self.assertRaisesRegex(ValueError, 'cooldown'):
            run(state(player_lightning_available_at_second=63600), 1000, [op('ability', 60000, 'bolt')])

    def test_death_prevention_uses_declared_effect_and_charge(self):
        operations = [op('combat_start', 60000), op('ability', 60000, 'dawn_shield'),
                      op('prevent_death', 60030, amount=40)]
        after = run(state(player_hp=30), 1001, operations)
        self.assertEqual((after['player_hp'], after['player_death_refusal_available']), (1, 0))
        with self.assertRaisesRegex(ValueError, 'Lethal prevention'):
            run(state(player_hp=30), 1001, [op('combat_start', 60000), op('prevent_death', 60030, amount=40)])

    def test_resurrection_requires_its_form_and_charge(self):
        dead = state(1100, player_hp=0, night_id=1, player_resurrections_left=1)
        self.assertEqual(run(dead, 1100, [op('resurrection', 66000)])['player_hp'], 50)
        with self.assertRaisesRegex(ValueError, 'form'):
            run(state(player_hp=0, player_resurrections_left=1), 1000, [op('resurrection', 60000)])

    def test_engine_resources_refuse_free_changes(self):
        change = {'operation': 'set', 'path': '/resources/player_storms_left', 'value': 1}
        with self.assertRaisesRegex(ValueError, 'Free mechanical mutation'):
            run(state(), 1000, [], [change])

    def test_cooldown_without_mechanics(self):
        config = {k: v for k, v in CONFIG.items() if k != 'mechanics'}
        before, after = state(), state(1001, player_lightning_available_at_second=1001 * 60 + 3600)
        cooldowns.validate_transition(before, after, config)
        with self.assertRaisesRegex(ValueError, 'bolt'):
            cooldowns.validate_transition(before, state(1001, player_lightning_available_at_second=5), config)

    def test_character_transition_checks_declared_charges(self):
        with self.assertRaisesRegex(ValueError, 'Night charge refilled'):
            validate_character_transition(state(), state(1001, player_storms_left=1), CONFIG)
        with self.assertRaisesRegex(ValueError, 'same combat'):
            validate_character_transition(state(), state(1001, player_death_refusal_available=1), CONFIG)
        missing = state(1001)
        del missing['resources']['player_storms_left']
        with self.assertRaisesRegex(ValueError, 'player_storms_left'):
            validate_character_transition(state(), missing, CONFIG)

    def test_skill_sheet_uses_configured_names(self):
        current = state()
        current['powers'] = {key: {'actor_id': 'player', 'audience': ['player'], 'description': key + ' : effect',
                                   'limits': 'none'} for key in ('lantern', 'bolt', 'dawn_shield')}
        result = abilities.packet(current, CONFIG)
        sheets = {item['id']: item for item in result['abilities']}
        self.assertEqual((sheets['lantern']['name'], sheets['lantern']['default_hp_cost']), ('Watch lantern', 0))
        self.assertEqual(sheets['dawn_shield']['charge'], {'left': 0, 'period': 'combat'})
        self.assertEqual(sheets['bolt']['cooldown']['seconds_left'], 0)
        self.assertTrue(abilities.render(result).startswith('Mira: 100/100 HP.'))

    def test_projection_only_catalogues_declared_abilities(self):
        original = {'current_state': {'powers': {'bolt': {'description': 'Lightning', 'references': ['r1'],
                                                           'audience': ['player']},
                                                'storm': {'description': 'Storm', 'audience': ['player']}},
                                     'effects': {}},
                    'campaign_rules': {'character_rules': deepcopy(RULES)}}
        packet = deepcopy(original)
        with mock.patch.dict(os.environ, {'NOVEL_TARGETED_CONTEXT': '1', 'NOVEL_CONTEXT_REDUCTIONS': ''}):
            targeted_context._project_powers(packet, [], set())
            self.assertEqual(list(packet['current_state']['powers']), ['storm'])
            self.assertIn('bolt', packet['power_catalogue'])
            cited = {**deepcopy(original), 'player_input': 'I cast lightning'}
            self.assertIn('bolt', targeted_context.analysis(cited)[1])

    def test_index_mode_keeps_every_sheet_when_hp_is_low(self):
        def packet(hp):
            return {'current_state': {'powers': {'bolt': {'description': 'Lightning', 'audience': ['player']},
                                                 'storm': {'description': 'Storm', 'audience': ['player']}},
                                      'effects': {}, 'resources': {'player_hp': hp, 'player_hp_max': 100}},
                    'campaign_rules': {'character_rules': deepcopy(RULES)}, 'player_input': 'I look around'}
        env = {'NOVEL_TARGETED_CONTEXT': '1', 'NOVEL_CONTEXT_REDUCTIONS': 'index,danger_hp'}
        with mock.patch.dict(os.environ, env):
            wounded, healthy = targeted_context.project(packet(30)), targeted_context.project(packet(100))
        self.assertIn('bolt', wounded['current_state']['powers'])
        self.assertNotIn('bolt', healthy['current_state']['powers'])

    def test_character_rules_without_charges_need_no_night_or_combat_resources(self):
        config = {'character_rules': {'hp_max': 80, 'forbidden_resources': ['player_hunger']}}
        before = {'world_minute': 0, 'resources': {'player_hp': 80, 'player_hp_max': 80}}
        validate_character_transition(before, deepcopy(before), config)
        with self.assertRaisesRegex(ValueError, 'forbidden'):
            validate_character_transition(before, {**before, 'resources': {**before['resources'], 'player_hunger': 1}}, config)
        # Without an explicit hp_max in the rules, the maximum is simply frozen.
        config = {'character_rules': {}}
        config['character_rules'] = {'forbidden_resources': []}
        validate_character_transition(before, deepcopy(before), config)
        with self.assertRaisesRegex(ValueError, 'maximum HP'):
            validate_character_transition(before, {**before, 'resources': {**before['resources'], 'player_hp_max': 99}}, config)


if __name__ == '__main__':
    unittest.main()
